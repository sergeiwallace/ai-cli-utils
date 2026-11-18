"""A task-prefix conflict only matters for projects that exist on this host.

Registries are shared across machines, so a registry can name a project that has no
checkout here. When such an absent project shares a prefix with a project that IS
here, the clash cannot mislead anything on this machine: only the present project can
be launched. Refusing every launch over it blocks the user for a conflict that is
someone else's host to resolve. These tests pin the host-presence rule: a conflict
between two present projects is still refused, a conflict with an absent project
warns once and the present project owns the prefix.

Every case reads real registry files and real directories; the registry reader is not
mocked, so the rule cannot pass while the reader disagrees.
"""

from __future__ import annotations

import tomllib

import pytest

from ai_cli import config
from ai_cli.config import FLEET_REGISTRY_MARKER, ProjectPrefixError, registered_project_names


def _write_registry(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            f'[[projects]]\nname = "{name}"\ntask_prefix = "{prefix}"\ntype = "tool"\nactive = true\n\n'
            for name, prefix in entries.items()
        ),
        encoding="utf-8",
    )


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A projects directory, a fleet registry and a separate per-machine registry."""
    projects_dir = tmp_path / "projects"
    fleet_registry = projects_dir / "myconfig" / FLEET_REGISTRY_MARKER
    local_registry = tmp_path / "local" / "local.toml"
    xdg = tmp_path / "xdg"
    xdg.mkdir()

    def _load_config():
        config_file = xdg / "config.toml"
        if not config_file.is_file():
            return {}
        with config_file.open("rb") as handle:
            return tomllib.load(handle)

    monkeypatch.setattr("ai_cli.config.get_xdg_config_home", lambda: xdg)
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects_dir)
    monkeypatch.setattr("ai_cli.config.load_config", _load_config)
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: local_registry)
    monkeypatch.setattr("ai_cli.config._registry_cache", None)
    monkeypatch.setattr("ai_cli.config._warned_absent_alias_conflicts", set(), raising=False)
    (projects_dir / "myconfig").mkdir(parents=True)
    return projects_dir, fleet_registry, local_registry


def _present(projects_dir, *names):
    for name in names:
        (projects_dir / name).mkdir(parents=True, exist_ok=True)


def test_given_conflict_with_project_absent_here_when_listing_names_then_present_project_owns_prefix(host, capsys):
    projects_dir, fleet_registry, local_registry = host
    _write_registry(fleet_registry, {"myworkspace": "ws"})
    _write_registry(local_registry, {"myapp": "ws"})
    _present(projects_dir, "myapp")

    names = registered_project_names()

    assert names["ws"] == "myapp"
    captured = capsys.readouterr()
    assert "myworkspace" in captured.out + captured.err


def test_given_conflict_with_absent_project_when_listing_twice_then_warns_once(host, capsys):
    projects_dir, fleet_registry, local_registry = host
    _write_registry(fleet_registry, {"myworkspace": "ws"})
    _write_registry(local_registry, {"myapp": "ws"})
    _present(projects_dir, "myapp")

    registered_project_names()
    first = capsys.readouterr()
    config._registry_cache = None
    registered_project_names()
    second = capsys.readouterr()

    assert "myworkspace" in first.out + first.err
    assert "myworkspace" not in second.out + second.err


def test_given_conflict_between_two_present_projects_when_listing_names_then_it_is_refused(host):
    projects_dir, fleet_registry, local_registry = host
    _write_registry(fleet_registry, {"myworkspace": "ws"})
    _write_registry(local_registry, {"myapp": "ws"})
    _present(projects_dir, "myapp", "myworkspace")

    with pytest.raises(ProjectPrefixError, match="Conflicting project aliases"):
        registered_project_names()


def test_given_conflict_where_neither_project_is_here_when_listing_names_then_it_warns_without_refusing(host, capsys):
    _projects_dir, fleet_registry, local_registry = host
    _write_registry(fleet_registry, {"myworkspace": "ws"})
    _write_registry(local_registry, {"myapp": "ws"})

    names = registered_project_names()

    assert names["ws"] in {"myworkspace", "myapp"}
    captured = capsys.readouterr()
    assert "myworkspace" in captured.out + captured.err
    assert "myapp" in captured.out + captured.err


def test_given_absent_project_conflict_when_validating_launcher_pair_then_present_project_is_accepted(host):
    projects_dir, fleet_registry, local_registry = host
    _write_registry(fleet_registry, {"myworkspace": "ws"})
    _write_registry(local_registry, {"myapp": "ws"})
    _present(projects_dir, "myapp")

    config.validate_remote_project("myapp", "ws")
