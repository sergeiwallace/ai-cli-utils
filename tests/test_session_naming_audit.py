"""The naming census finds slots that have neither transcripts nor registry rows."""

import json

import pytest
from conftest import run_cli

from ai_cli import config, name_registry, session_audit, session_naming


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    repos = {
        alias: projects / name for alias, name in [("myrepo", "myproject"), ("other", "otherproject"), ("app", "myapp")]
    }
    for repo in repos.values():
        (repo / ".worktrees").mkdir(parents=True)
    settings = {"project_registry": {str(repo): {"prefix": alias} for alias, repo in repos.items()}}
    monkeypatch.setattr(config, "load_config", lambda: settings)
    monkeypatch.setattr(config, "_get_projects_dir", lambda: projects)
    monkeypatch.setattr(session_naming, "host_id", lambda: "test-host")
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    state = tmp_path / "state"
    (state / "sessions").mkdir(parents=True)
    home = tmp_path / "claude"
    snapshot = name_registry.Snapshot(tip=None)
    return repos, snapshot, state, home, settings


def test_given_seven_unrecorded_slots_when_audited_then_all_and_stale_alias_are_found(inventory):
    repos, snapshot, state, home, _ = inventory
    slots = [
        ("myrepo", "myrepo-tst-2"),
        ("myrepo", "myrepo-tst-3"),
        ("other", "other-tst-2"),
        ("other", "other-tst-3"),
        ("app", "app-tst-2"),
        ("app", "app-tst-4"),
        ("myrepo", "old-tst-1"),
    ]
    for alias, title in slots:
        (repos[alias] / ".worktrees" / title).mkdir()
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    missing = {f.name for f in findings if f.rule == "missing-registry-row"}
    assert missing == {title for _, title in slots}
    assert any(f.name == "old-tst-1" and f.rule == "repo-alias" for f in findings)


def test_given_diagnostic_and_container_when_audited_then_neither_is_a_canonical_slot(inventory):
    repos, snapshot, state, home, _ = inventory
    base = repos["myrepo"] / ".worktrees"
    for title in ("myrepo-tst-wake-probe-1", "myrepo-tst-test-1", "myrepo-tst-1-agents"):
        (base / title).mkdir()
    findings = session_audit.audit_naming(
        repos=[repos["myrepo"]], snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert {f.name for f in findings if f.rule == "noncanonical"} == {"myrepo-tst-wake-probe-1", "myrepo-tst-test-1"}
    assert not any(f.rule == "missing-registry-row" for f in findings)


def test_given_orphans_and_disagreements_when_audited_then_each_boundary_is_reported(inventory):
    repos, snapshot, state, home, _ = inventory
    repo = repos["myrepo"]
    title = "myrepo-tst-cleo"
    slot = repo / ".worktrees" / title
    slot.mkdir()
    (repo / ".worktrees" / ".myrepo-tst-9.lock").touch()
    snapshot.rows = {
        "cleo": {
            "kind": "session",
            "name": "cleo",
            "title": title,
            "repo": repo.name,
            "repo_alias": "old",
            "host_alias": "tst",
            "machine": "test-host",
            "worktree": f".worktrees/{title}",
        },
        "clea": {"kind": "spare", "name": "clea", "host_alias": "tst", "machine": "test-host"},
    }
    (state / "sessions" / "c-myrepo-tst-9.sh").write_text("# orphan\n")
    (state / "sessions" / f"c-{title}.sh").write_text(f"ai_name={title}\n")
    (state / f"session-meta-c-{title}.json").write_text(
        json.dumps({"ai_name": title, "engine": "c", "session": "c-myrepo-tst-2", "worktree_dir": str(slot)})
    )
    findings = session_audit.audit_naming(
        repos=[repo], snapshot=snapshot, state=state, claude_home=home, tmux_names=["c-myrepo-tst-9"]
    )
    assert {"repo-alias", "name-distance", "orphan-script", "orphan-lock", "tmux-agreement"} <= {
        f.rule for f in findings
    }


def test_given_no_transcripts_when_cli_naming_audit_runs_then_slots_still_appear(inventory):
    repos, snapshot, state, home, settings = inventory
    title = "myrepo-tst-1"
    (repos["myrepo"] / ".worktrees" / title).mkdir()
    code, out, err = run_cli(["ai", "audit-sessions", "--naming", "--repo", str(repos["myrepo"])], config=settings)
    assert code == 1, out + err
    assert "missing-registry-row" in out and title in out


def test_given_uninitialized_local_registry_when_reading_census_then_no_files_are_created(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    monkeypatch.setattr(name_registry, "discover", lambda: name_registry.Discovery(name_registry.LocalRegistry(root)))
    assert session_naming.naming_snapshot().rows == {}
    assert not root.exists()


def test_given_legacy_state_when_naming_census_runs_then_configuration_and_state_are_unchanged(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    (cfg / "ai-cli-utils").mkdir(parents=True)
    config_path = cfg / "ai-cli-utils" / "config.toml"
    config_path.write_text('[project]\nprojects_dir = "' + tmp_path.as_posix() + '"\n')
    legacy = tmp_path / "state" / "ai-cli"
    legacy.mkdir(parents=True)
    (legacy / "marker").write_text("keep\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(legacy.parent))
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    before = config_path.read_bytes()
    session_audit.audit_naming(repos=[], snapshot=name_registry.Snapshot(tip=None), tmux_names=[])
    assert legacy.exists(), "read-only audit migrated legacy state"
    assert config_path.read_bytes() == before, "read-only audit rewrote configuration"


def test_given_legacy_state_with_launcher_when_audited_then_legacy_files_are_still_inspected(tmp_path, monkeypatch):
    legacy = tmp_path / "state" / "ai-cli"
    scripts = legacy / "sessions"
    scripts.mkdir(parents=True)
    (scripts / "c-myrepo-tst-1.sh").write_text("# orphan\n")
    monkeypatch.setenv("XDG_STATE_HOME", str(legacy.parent))
    findings = session_audit.audit_naming(
        repos=[], snapshot=name_registry.Snapshot(tip=None), tmux_names=[], claude_home=tmp_path / "claude"
    )
    assert any(f.rule == "orphan-script" and f.name == "myrepo-tst-1" for f in findings)
    assert legacy.exists()


def test_given_launcher_content_disagrees_when_audited_then_script_identity_is_reported(inventory):
    repos, snapshot, state, home, _ = inventory
    title = "myrepo-tst-1"
    (repos["myrepo"] / ".worktrees" / title).mkdir()
    (state / "sessions" / f"c-{title}.sh").write_text("ai_name=myrepo-tst-2\ntmux_session=c-myrepo-tst-1\n")
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert any(f.rule == "script-agreement" and f.name == title for f in findings)


def test_given_title_without_slot_when_audited_then_owner_alias_is_checked(inventory):
    from test_session_audit import _write_transcript

    repos, snapshot, state, home, _ = inventory
    _write_transcript(home, "aaaaaaaa-2222-4333-8444-555555555555", "old-tst-1", repos["myrepo"])
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert any(f.rule == "repo-alias" and f.name == "old-tst-1" for f in findings)


def test_given_native_task_namespace_when_transcript_exists_then_it_is_not_an_orphan(inventory):
    from ai_cli.cc_migrate import cc_project_dir
    from ai_cli.session_adopt import task_namespace_candidates

    repos, snapshot, state, home, _ = inventory
    uuid = "aaaaaaaa-2222-4333-8444-555555555555"
    project = cc_project_dir(repos["myrepo"], home)
    project.mkdir(parents=True)
    (project / f"{uuid}.jsonl").write_text("{}\n")
    for name in task_namespace_candidates(uuid):
        (home / "tasks" / name).mkdir(parents=True)
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert not any(f.rule == "orphan-task-namespace" for f in findings)


def test_given_host_qualified_cross_repo_container_when_owner_is_registered_then_aliases_are_checked(inventory):
    repos, snapshot, state, home, _ = inventory
    source = session_naming.alias_sources()[0]
    source.write_text('[aliases]\ntest-host = "tst"\nremote-host = "two"\n')
    title = "other-two-cleo"
    snapshot.rows["cleo"] = {
        "kind": "session",
        "name": "cleo",
        "title": title,
        "repo": "otherproject",
        "repo_alias": "other",
        "host_alias": "two",
        "machine": "remote-host",
        "worktree": f".worktrees/{title}",
    }
    (repos["myrepo"] / ".worktrees" / f"remote-host--{title}-agents").mkdir()
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert not any(f.rule in {"host-alias", "unregistered-container", "repo-alias"} for f in findings)


def test_given_numeric_slot_in_canonical_registry_when_audited_then_registration_and_identity_are_checked(inventory):
    from ai_cli.canonical_worktrees import register_canonical_worktree

    repos, snapshot, state, home, _ = inventory
    title = "myrepo-tst-1"
    slot = repos["myrepo"] / ".worktrees" / title
    slot.mkdir()
    register_canonical_worktree(slot, engine="c", session_name="c-myrepo-tst-2")
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert not any(f.rule == "missing-registry-row" for f in findings)
    assert any(f.rule == "tmux-agreement" and f.name == title for f in findings)


def test_given_diagnostic_word_is_repo_alias_when_audited_then_numeric_home_still_requires_registration(inventory):
    repos, snapshot, state, home, settings = inventory
    repo = repos["myrepo"]
    settings["project_registry"][str(repo)]["prefix"] = "test"
    (repo / ".worktrees" / "test-tst-1").mkdir()
    findings = session_audit.audit_naming(repos=[repo], snapshot=snapshot, state=state, claude_home=home, tmux_names=[])
    assert any(f.rule == "missing-registry-row" and f.name == "test-tst-1" for f in findings)


def test_given_numeric_owner_registered_when_cross_repo_container_is_audited_then_owner_is_found(inventory):
    from ai_cli.canonical_worktrees import register_canonical_worktree

    repos, snapshot, state, home, _ = inventory
    title = "other-tst-1"
    slot = repos["other"] / ".worktrees" / title
    slot.mkdir()
    register_canonical_worktree(slot, engine="c", session_name=f"c-{title}")
    (repos["myrepo"] / ".worktrees" / f"{title}-agents").mkdir()
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert not any(f.rule == "unregistered-container" for f in findings)


def test_given_hook_registered_title_when_audited_then_it_is_not_misread_as_tmux_name(inventory):
    from ai_cli.canonical_worktrees import register_canonical_worktree

    repos, snapshot, state, home, _ = inventory
    repo = repos["myrepo"]
    title = "myrepo-tst-1"
    slot = repo / ".worktrees" / title
    slot.mkdir()
    register_canonical_worktree(slot, engine="claude-session-start", session_name=title)
    findings = session_audit.audit_naming(repos=[repo], snapshot=snapshot, state=state, claude_home=home, tmux_names=[])
    assert not any(f.rule == "tmux-agreement" for f in findings)


def test_given_root_cwd_without_worktree_when_auditing_metadata_then_no_slot_mismatch_is_reported(inventory):
    repos, snapshot, state, home, _ = inventory
    title = "myrepo-tst-1"
    (state / f"session-meta-c-{title}.json").write_text(
        json.dumps({"engine": "c", "ai_name": title, "session": f"c-{title}", "worktree_dir": str(repos["myrepo"])})
    )
    findings = session_audit.audit_naming(
        repos=list(repos.values()), snapshot=snapshot, state=state, claude_home=home, tmux_names=[]
    )
    assert not any(f.rule == "worktree-agreement" for f in findings)
