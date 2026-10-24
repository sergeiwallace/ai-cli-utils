"""Real naming entry points must refresh a retired shell identity before lookup."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli, with_environment_config
from test_host_env import host_tools  # noqa: F401
from test_session_audit import _make_repo, _write_transcript
from test_session_auto_migrate import _launch as _migrate_launch
from test_session_migrate import slot  # noqa: F401
from test_session_naming import _planned_session, _write_aliases, real_alias_lookup  # noqa: F401

from ai_cli import chief_of_staff, host_env, name_registry

_REAL_RESOLVE_HOST_ENV = host_env.resolve_host_env


@pytest.fixture
def fresh_host(real_alias_lookup, host_tools, monkeypatch):  # noqa: F811
    configure, bin_dir = host_tools
    for name in ("pi", "codex"):
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
    configure('printf "AI_HOST_FRESH=new-host\\n"', "exit 1")
    monkeypatch.setenv("AI_HOST", "old-host")
    monkeypatch.setattr(host_env, "resolve_host_env", _REAL_RESOLVE_HOST_ENV)
    _write_aliases(real_alias_lookup["environment"], '[aliases]\nnew-host = "box"\n')
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    return real_alias_lookup, configure


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
def test_given_stale_host_when_engine_launches_then_current_alias_names_session(fresh_host, engine):
    with (
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/myrepo"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
    ):
        code, out, err = run_cli(["ai", engine, "1", "--project-prefix", "myrepo", "--dry-run"])
    assert code == 0, err
    assert _planned_session(out) == "myrepo-box-1"


@pytest.fixture
def chief_home(fresh_host, tmp_path, monkeypatch):
    project = fresh_host[0]["projects"] / "myhub"
    monkeypatch.chdir(project)
    monkeypatch.setattr("ai_cli.config._find_project_dir", lambda name, _home=None: project)
    monkeypatch.setattr("ai_cli.config.resolve_project_prefix_by_name", lambda name: "myrepo")
    home = tmp_path / "chief-home"
    (home / "config").mkdir(parents=True)
    (home / "state").mkdir()
    (home / "config" / "message-transports.json").write_text(
        '{"schema_version":1,"primary":"native","fallbacks":["agent-mail","fm-send"]}\n'
    )
    return home


def _chief(home, config=None):
    return run_cli(["ai", "cos", "1", "--fm-home", str(home), "--dry-run"], config=with_environment_config(config))


def test_given_stale_host_when_chief_launches_then_current_alias_names_session(chief_home):
    code, out, err = _chief(chief_home)
    assert code == 0, err
    assert "myrepo-box-chief-1" in out
    assert not chief_of_staff.registration_path(chief_home).exists()


@pytest.mark.parametrize(
    ("alias", "legacy", "expected"),
    [
        (True, "full", "myrepo-box-chief-1"),
        (True, "off", "myrepo-box-chief-1"),
        (False, "full", "myrepo-chief-1"),
        (False, "off", None),
    ],
)
def test_given_convention_mode_when_chief_launches_then_config_controls_name(chief_home, alias, legacy, expected):
    code, out, err = _chief(chief_home, {"naming": {"alias_convention": alias, "legacy_convention": legacy}})
    assert code == (0 if expected else 1), err
    if expected:
        assert expected in out
    else:
        assert "naming.alias_convention" in err and "naming.legacy_convention" in err
    assert not chief_of_staff.registration_path(chief_home).exists()


@pytest.mark.parametrize("legacy", ["full", "off", "reach"])
def test_given_unmapped_hosts_when_chief_launches_then_fallback_or_one_actionable_refusal(
    chief_home, fresh_host, legacy
):
    _write_aliases(fresh_host[0]["environment"], '[aliases]\nother-host = "box"\n')
    code, out, err = _chief(chief_home, {"naming": {"legacy_convention": legacy}})
    assert code == (0 if legacy == "full" else 1), err
    if legacy == "full":
        assert "myrepo-chief-1" in out
    else:
        assert "new-host" in err and "old-host" in err and "naming.legacy_convention" in err
        assert err.count("Error:") == 1
        assert "Traceback" not in err
    assert not chief_of_staff.registration_path(chief_home).exists()


def test_given_stale_host_when_name_candidates_listed_then_current_alias_is_resolved(fresh_host):
    code, out, err = run_cli(["ai", "list-name-candidates", "--repo", "myrepo"])
    assert code == 0, err
    assert "free" in out
    assert os.environ["AI_HOST"] == "new-host"


def test_given_stale_host_when_migrating_to_name_then_current_alias_names_target(fresh_host, tmp_path):
    repo = _make_repo(tmp_path, "myrepo")
    (repo / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "myrepo"\n')
    code, out, err = run_cli(
        ["ai", "migrate-session", "myrepo-1", "--to-name", "cleo", "--dry-run", "--repo", str(repo)]
    )
    assert code == 0, err
    assert "myrepo-box-cleo" in out


def test_given_stale_host_when_sessions_audited_then_current_alias_identifies_label(fresh_host, tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, "myrepo")
    (repo / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "myrepo"\n')
    home = Path.home() / ".claude"
    _write_transcript(home, "aaaaaaaa-2222-4333-8444-555555555555", "myrepo-box-review-ada", repo)
    monkeypatch.setattr("ai_cli.name_registry.known_names", lambda registry=None: frozenset({"ada"}))
    code, out, err = run_cli(["ai", "audit-sessions", "--repo", str(repo)])
    assert code == 0, err
    assert "label=review" in out


def test_given_stale_host_when_names_reconciled_then_current_host_owns_release(fresh_host):
    registry = name_registry.discover().registry
    assert registry is not None
    reservation_slot = name_registry.Slot(
        repo="myrepo", repo_alias="myrepo", host_alias="box", label=None, machine="new-host", client="claude-code"
    )
    name_registry.reserve_hand_chosen(
        registry, "cleo", reservation_slot, add=True, via="add-name", added_by="cli", words=frozenset()
    )
    code, out, err = run_cli(["ai", "reconcile-names", "--dry-run", "--json"])
    assert code == 0, err
    assert json.loads(out)["released"] == ["cleo"]


@pytest.mark.usefixtures("interactive_terminal")
def test_given_stale_host_when_legacy_launch_migrates_then_current_alias_names_target(slot, fresh_host):  # noqa: F811
    code, out, err, launched = _migrate_launch(slot, real_aliases=True)
    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == ["myrepo-box-3"]
    assert not slot["wt"].exists()
