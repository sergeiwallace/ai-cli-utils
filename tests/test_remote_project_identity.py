"""Project selection must agree across every registry used by a launch."""

import pytest
from conftest import run_cli

from ai_cli import config, main


@pytest.fixture
def project_registries(tmp_path, monkeypatch):
    fleet = tmp_path / "fleet.toml"
    fleet.write_text('[[projects]]\nname = "project-a"\ntask_prefix = "pa"\n')
    # A prefix clash is refused only when both projects are checked out on this host, so these
    # projects exist on disk: every conflict below is a real one for this machine.
    projects_dir = tmp_path / "projects"
    for name in ("project-a", "project-b", "pa"):
        (projects_dir / name).mkdir(parents=True)
    monkeypatch.setattr(config, "_get_projects_dir", lambda: projects_dir)
    monkeypatch.setattr(config, "get_fleet_registry_path", lambda: fleet)
    monkeypatch.setattr(config, "load_project_registry", list)
    monkeypatch.setattr(config, "_registry_entries", dict)
    return fleet


def test_given_root_registry_conflict_when_aliases_resolve_then_neither_project_wins(
    project_registries, monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "_registry_entries", lambda: {str(tmp_path / "project-b"): {"prefix": "PA"}})
    with pytest.raises(config.ProjectPrefixError, match=r"project-a.*project-b|project-b.*project-a"):
        config.get_project_aliases()


def test_given_self_named_prefix_conflict_when_aliases_resolve_then_conflict_is_reported(project_registries):
    project_registries.write_text('[[projects]]\nname = "pa"\ntask_prefix = "pa"\n')
    from unittest.mock import patch

    with patch.object(config, "load_project_registry", return_value=[{"name": "project-b", "task_prefix": "PA"}]):
        with pytest.raises(config.ProjectPrefixError, match=r"pa.*project-b|project-b.*pa"):
            config.get_project_aliases()


def test_given_profile_disagreement_when_remote_allocates_then_it_refuses_before_reserving(
    project_registries, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("a conflicting project reached session allocation")

    monkeypatch.setattr(main, "_resolve_session_identity", forbidden)
    code, _, output = run_cli(
        ["ai", "internal", "allocate-session-name", "c", "pa", "alpha", "--project", "project-b"], config={}
    )
    assert code == 1
    assert "project-a" in output and "project-b" in output and "Fix:" in output


def test_given_duplicate_legacy_prefix_when_aliases_resolve_then_both_projects_and_remedy_are_reported(
    tmp_path, monkeypatch, capsys
):
    legacy = tmp_path / "legacy.toml"
    legacy.write_text(
        '[[projects]]\nname = "project-a"\ntask_prefix = "pa"\n[[projects]]\nname = "project-b"\ntask_prefix = "PA"\n'
    )
    monkeypatch.setattr(config, "get_fleet_registry_path", lambda: None)
    monkeypatch.setattr(config, "_get_project_registry_path", lambda: legacy)
    monkeypatch.setattr(config, "_registry_cache", None)
    with pytest.raises(SystemExit) as error:
        config.get_project_aliases()
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "project-a" in output and "project-b" in output and "Fix:" in output
