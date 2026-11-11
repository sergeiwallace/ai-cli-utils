"""Names are refused before filesystem or launcher state can be created."""

import json

import pytest
from conftest import run_cli
from test_migrate_naming_validation import registered_repo as registered_repo

from ai_cli import config, main, session, session_naming


@pytest.mark.parametrize("existing_config", [False, True])
def test_given_uninitialised_config_when_validating_artifact_then_config_and_output_are_unchanged(
    tmp_path, monkeypatch, register_repo, capsys, existing_config
):
    repo = tmp_path / "myproject"
    repo.mkdir()
    register_repo(repo, "myrepo")
    config_home = tmp_path / "config"
    config_home.mkdir()
    config_file = config_home / "config.toml"
    if existing_config:
        config_file.write_text('[machine]\nhost_id = "test-host"\n', encoding="utf-8")
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: config_home)
    monkeypatch.setattr(config, "config_file_text", lambda: str(config_file))
    before = {path: path.read_bytes() for path in config_home.rglob("*") if path.is_file()}

    parsed = session_naming.validate_artifact_name("myrepo-tst-1", repo)

    assert parsed.canonical
    assert {path: path.read_bytes() for path in config_home.rglob("*") if path.is_file()} == before
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("value", ["bad/name", "bad_name", "Réview", "UPPER", "bad--name", "0", "01"])
def test_given_malformed_input_when_launching_then_refused_without_sanitizing(registered_repo, value):
    repo, settings = registered_repo
    code, out, err = run_cli(["ai", "c", value, "--dry-run"], config=settings)
    assert code != 0, f"accepted malformed input {value!r}: {out}{err}"
    assert "session charset" in err or "session index" in err
    assert not (repo / ".worktrees").exists()


def test_given_wrong_override_when_launching_then_registered_alias_wins(registered_repo):
    repo, settings = registered_repo
    code, out, err = run_cli(["ai", "c", "1", "--project-prefix", "wrong", "--dry-run"], config=settings)
    assert code != 0, f"accepted wrong repo alias: {out}{err}"
    assert "repo alias" in err and "myrepo" in err and "wrong" in err
    assert not (repo / ".worktrees").exists()


def test_given_explicit_project_with_wrong_override_when_launching_then_registered_alias_wins(
    registered_repo, monkeypatch
):
    repo, settings = registered_repo
    monkeypatch.setattr(config, "_find_project_dir", lambda name: repo)
    code, out, err = run_cli(
        ["ai", "c", "1", "--project", "myrepo", "--project-prefix", "wrong", "-b", "--dry-run"], config=settings
    )
    assert code == 1, f"accepted wrong explicit-project alias: {out}{err}"
    assert "repo alias rule" in err


@pytest.mark.parametrize("title", ["wrong-tst-1", "myrepo-other-1", "myrepo-tst-0", "myrepo-tst-Éva"])
def test_given_wrong_name_when_creating_worktree_then_refused_before_git(registered_repo, title):
    repo, _ = registered_repo
    with pytest.raises(session_naming.HostAliasError, match="rule"):
        session.create_worktree(title, repo_root=repo)
    assert not (repo / ".worktrees").exists()


@pytest.mark.parametrize(
    ("title", "tmux"),
    [("wrong-tst-1", "c-wrong-tst-1"), ("myrepo-other-1", "c-myrepo-other-1"), ("myrepo-tst-1", "c-myrepo-tst-2")],
)
def test_given_disagreeing_identity_when_generating_script_then_refused(registered_repo, title, tmux):
    repo, _ = registered_repo
    with pytest.raises(session_naming.HostAliasError, match="rule"):
        main._engine_script_from_meta(
            {
                "engine": "c",
                "ai_name": title,
                "session": tmux,
                "prefix": "c-myrepo-tst-",
                "project_prefix": "myrepo",
                "worktree_dir": str(repo / ".worktrees" / title),
            }
        )


@pytest.mark.parametrize("title", ["wrong-tst-1", "myrepo-other-1", "myrepo-tst-0", "myrepo-tst-Éva"])
def test_given_wrong_metadata_when_rewriting_launcher_then_existing_script_is_preserved(
    registered_repo, tmp_path, monkeypatch, capsys, title
):
    repo, _ = registered_repo
    state = tmp_path / "state"
    scripts = state / "sessions"
    scripts.mkdir(parents=True)
    target = f"c-{title}"
    script = scripts / f"{target}.sh"
    script.write_text("keep this launcher\n")
    (state / f"session-meta-{target}.json").write_text(
        json.dumps(
            {
                "engine": "c",
                "ai_name": title,
                "session": target,
                "prefix": "c-myrepo-tst-",
                "project_prefix": "myrepo",
                "worktree_dir": str(repo / ".worktrees" / title),
            }
        )
    )
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: state)
    assert not main._write_stable_session_script(target)
    assert script.read_text() == "keep this launcher\n"
    assert "rule refused" in capsys.readouterr().err


def test_given_registered_repo_root_when_rewriting_no_worktree_launcher_then_root_cwd_is_supported(registered_repo):
    repo, _ = registered_repo
    script = main._engine_script_from_meta(
        {
            "engine": "c",
            "ai_name": "myrepo-tst-1",
            "session": "c-myrepo-tst-1",
            "prefix": "c-myrepo-tst-",
            "project_prefix": "myrepo",
            "worktree_dir": str(repo),
        }
    )
    assert f"cd -- {repo}" in script


@pytest.mark.parametrize("title", ["myrepo-tst-1", "myrepo-tst-review-2", "myrepo-tst-cleo"])
def test_given_valid_identity_when_validating_then_parts_round_trip(title):
    parsed = session_naming.validate_session_name(title, repo_alias="myrepo", host_alias="tst", names={"cleo"})
    assert parsed.title == title
    assert parsed.canonical


def test_given_remote_identity_when_validating_then_owning_host_is_used(registered_repo):
    repo, _ = registered_repo
    source = session_naming.alias_sources()[0]
    source.write_text('[aliases]\ntest-host = "tst"\nremote-host = "two"\n')
    parsed = session_naming.validate_artifact_name("myrepo-two-1", repo, machine="remote-host")
    assert parsed.canonical
    with pytest.raises(session_naming.HostAliasError, match="host alias"):
        session_naming.validate_artifact_name("myrepo-two-1", repo)


def test_given_unregistered_repo_when_validating_then_no_registration_is_written(tmp_path):
    with pytest.raises(session_naming.HostAliasError, match="repo registration"):
        session_naming.validate_artifact_name("myrepo-tst-1", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_given_alias_changed_when_validating_then_stale_alias_is_refused(registered_repo):
    repo, settings = registered_repo
    settings["project_registry"][str(repo)]["prefix"] = "new"
    with pytest.raises(session_naming.HostAliasError, match="repo alias"):
        session_naming.validate_artifact_name("myrepo-tst-1", repo)
    assert session_naming.validate_artifact_name("new-tst-1", repo).canonical


@pytest.mark.parametrize("title", ["myrepo-tst-agents", "myrepo-tst-tst", "myrepo-tst-myrepo"])
def test_given_reserved_suffix_when_validating_then_human_name_is_refused(title):
    with pytest.raises(session_naming.HostAliasError, match="reserved name"):
        session_naming.validate_session_name(
            title, repo_alias="myrepo", host_alias="tst", names={title.rsplit("-", 1)[-1]}
        )


def test_given_missing_host_when_validating_then_explicit_alias_is_required(registered_repo):
    repo, _ = registered_repo
    session_naming.alias_sources()[0].write_text("[aliases]\n")
    with pytest.raises(session_naming.HostAliasNotConfiguredError, match="no host alias"):
        session_naming.validate_artifact_name("myrepo-tst-1", repo)


def test_given_reserved_alias_when_hand_choosing_then_registry_refuses(tmp_path, monkeypatch):
    from name_fleet import build_fleet

    from ai_cli import name_registry as registry

    fleet = build_fleet(tmp_path, monkeypatch, names=["box"])
    slot = registry.Slot(
        repo="myrepo", repo_alias="myrepo", host_alias="box", label=None, machine="test-host", client="claude-code"
    )
    with pytest.raises(registry.HandChosenRefusedError, match="alias"):
        registry.reserve_hand_chosen(
            fleet.registry, "box", slot, add=False, via="add-name", added_by="cli", words=frozenset()
        )


@pytest.mark.parametrize("value", ["wrong-tst-1", "myrepo-other-1", "c-myrepo-other-1"])
def test_given_wrong_qualified_name_when_launching_then_alias_rule_refuses(registered_repo, value):
    repo, settings = registered_repo
    code, out, err = run_cli(["ai", "c", value, "-b", "--dry-run"], config=settings)
    assert code == 1, f"accepted wrong qualified name {value}: {out}{err}"
    assert "alias rule" in err
    assert not (repo / ".worktrees").exists()


def test_given_conflicting_aliases_when_reaching_legacy_home_then_conflict_is_not_ignored(
    registered_repo, tmp_path, monkeypatch
):
    repo, _ = registered_repo
    sources = [tmp_path / "one.toml", tmp_path / "two.toml"]
    for source, alias in zip(sources, ["tst", "two"], strict=True):
        source.write_text(f'[aliases]\ntest-host = "{alias}"\n')
    monkeypatch.setattr(session_naming, "alias_sources", lambda: sources)
    with pytest.raises(session_naming.HostAliasError, match="disagree"):
        session_naming.validate_artifact_name("myrepo-tst-1", repo, allow_legacy=True)


@pytest.mark.parametrize("value", ["wake-probe", "test"])
def test_given_diagnostic_label_when_launching_canonical_session_then_refused(registered_repo, value):
    _, settings = registered_repo
    code, out, err = run_cli(["ai", "c", value, "-b", "--dry-run"], config=settings)
    assert code == 1, f"accepted diagnostic canonical session: {out}{err}"
    assert "noncanonical rule" in err


@pytest.mark.parametrize(
    ("prefix", "name"), [("wrong", "1"), ("myrepo", "wrong-tst-1"), ("myrepo", "myrepo-other-1"), ("myrepo", "Bad")]
)
def test_given_wrong_remote_request_when_allocating_then_refused_before_reservation(
    registered_repo, monkeypatch, capsys, prefix, name
):
    repo, settings = registered_repo
    monkeypatch.setattr(config, "_find_project_dir", lambda value: repo)
    monkeypatch.setattr(main, "_resolve_session_identity", lambda *args, **kwargs: ("c-r-wrong-tst-1", "wrong-tst-1"))
    with pytest.raises(SystemExit) as caught:
        main._handle_internal(["allocate-session-name", "c", prefix, name, "--project", "myrepo"])
    assert caught.value.code == 1
    assert "rule refused" in capsys.readouterr().err


def test_given_metadata_for_another_session_when_rewriting_launcher_then_existing_script_is_preserved(
    registered_repo, tmp_path, monkeypatch, capsys
):
    repo, _ = registered_repo
    state = tmp_path / "state"
    scripts = state / "sessions"
    scripts.mkdir(parents=True)
    script = scripts / "c-myrepo-tst-1.sh"
    script.write_text("keep this launcher\n")
    (state / "session-meta-c-myrepo-tst-1.json").write_text(
        json.dumps(
            {
                "engine": "c",
                "ai_name": "myrepo-tst-2",
                "session": "c-myrepo-tst-2",
                "prefix": "c-myrepo-tst-",
                "project_prefix": "myrepo",
                "worktree_dir": str(repo / ".worktrees" / "myrepo-tst-2"),
            }
        )
    )
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: state)
    assert not main._write_stable_session_script("c-myrepo-tst-1")
    assert script.read_text() == "keep this launcher\n"
    assert "script agreement rule" in capsys.readouterr().err


def test_given_nearby_held_name_when_creating_home_then_refused_before_git(registered_repo, monkeypatch):
    from ai_cli import name_registry

    repo, _ = registered_repo
    snapshot = name_registry.Snapshot(
        tip=None,
        names=["cleo"],
        rows={
            "clea": {
                "kind": "session",
                "name": "clea",
                "title": "myrepo-tst-clea",
                "machine": session_naming.host_id(),
                "host_alias": "tst",
            }
        },
    )
    monkeypatch.setattr(session_naming, "naming_snapshot", lambda: snapshot, raising=False)
    with pytest.raises(session_naming.HostAliasError, match="name distance rule"):
        session.create_worktree("myrepo-tst-cleo", repo_root=repo)
    assert not (repo / ".worktrees").exists()


def test_given_human_names_disabled_when_validating_numeric_home_then_no_name_registry_is_required(
    registered_repo, monkeypatch
):
    from ai_cli import name_registry

    repo, _ = registered_repo
    monkeypatch.setattr(name_registry, "discover", lambda: name_registry.Discovery(None, "human names are disabled"))
    assert session_naming.validate_artifact_name("myrepo-tst-1", repo).canonical


def test_given_repo_metadata_when_resolving_in_inspection_mode_then_no_registration_is_written(registered_repo):
    repo, settings = registered_repo
    settings.clear()
    (repo / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "myrepo"\n')
    target = config.get_xdg_config_home() / "config.toml"
    before = target.read_bytes() if target.exists() else None
    with config.inspection_only():
        assert config.resolve_project_prefix(repo) == "myrepo"
    assert (target.read_bytes() if target.exists() else None) == before
