"""Tiered prefix resolution (AI-CLI-195).

Two prefix registries existed and did not know about each other: the
repository-root keyed ``[project_registry]`` table in the user's ``config.toml``,
which is what resolution read, and the ``[[projects]]`` registry named by
``[project] main_project``, which held the answers. A repository listed only in
the second one failed to resolve with "No task prefix is registered", while the
first accumulated one key per agent worktree as a side effect of being written on
every successful resolution.

These tests pin the resolution chain that replaced it: a persistent registry
first, the main-project registry, the config table, the repository's own
``[tool.ai-cli]`` metadata, then an interactive prompt that persists what it is
told. Every case is written against the public resolution functions rather than
the tier helpers, so a re-shuffle of the internals cannot pass a test that
resolution actually broke.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.config import (
    FLEET_REGISTRY_MARKER,
    ProjectPrefixError,
    get_fleet_registry_path,
    register_project,
    resolve_project_prefix,
    resolve_project_prefix_by_name,
)

_PROJECTS = ("myproject", "myapp", "mytool", "myservice", "mylib", "myworkspace", "mygraph")


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A projects directory with a config repository holding the persistent registry.

    Mirrors the real shape: sibling repositories under one projects directory, one
    of which carries ``config/fleet-projects.toml``. The XDG config home is
    redirected too, so nothing here can read or write the developer's real config.
    """
    projects_dir = tmp_path / "projects"
    for name in _PROJECTS:
        (projects_dir / name).mkdir(parents=True)
    config_repo = projects_dir / "myconfig"
    registry = config_repo / FLEET_REGISTRY_MARKER
    registry.parent.mkdir(parents=True)

    xdg = tmp_path / "xdg"
    xdg.mkdir()

    def _load_redirected_config():
        """Read the redirected config for real, so a persisted write is visible.

        Returning a fixed dict instead would make every round-trip assertion
        vacuous: the tier could write anywhere at all and the read-back would
        still report the stub.
        """
        import tomllib

        config_file = xdg / "config.toml"
        if not config_file.is_file():
            return {}
        with config_file.open("rb") as handle:
            return tomllib.load(handle)

    monkeypatch.setattr("ai_cli.config.get_xdg_config_home", lambda: xdg)
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects_dir)
    monkeypatch.setattr("ai_cli.config.load_config", _load_redirected_config)
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: None)
    return projects_dir, registry


def _write_projects_registry(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            f'[[projects]]\nname = "{name}"\ntask_prefix = "{prefix}"\ntype = "tool"\nactive = true\n\n'
            for name, prefix in entries.items()
        ),
        encoding="utf-8",
    )


# --- AC-1 / AC-2: the persistent registry resolves, and wins ---


def test_given_a_repo_in_the_persistent_registry_when_resolving_then_returns_its_prefix(fleet):
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})

    assert resolve_project_prefix(projects_dir / "myproject") == "MYPROJECT"


def test_given_a_repo_in_the_persistent_registry_when_resolving_from_elsewhere_then_still_resolves(
    fleet, tmp_path, monkeypatch
):
    """AC-2: the registry is found without its own repository being the cwd."""
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})
    unrelated = tmp_path / "somewhere-else"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    assert resolve_project_prefix(projects_dir / "myproject") == "MYPROJECT"


def test_given_all_three_tiers_disagree_when_resolving_then_the_persistent_registry_wins(fleet, monkeypatch):
    """Precedence, asserted where the tiers give DIFFERENT answers.

    With every tier agreeing, this test would pass against any precedence order at
    all -- including one that reads only the tier the bug read.
    """
    projects_dir, registry = fleet
    repo = projects_dir / "myproject"
    _write_projects_registry(registry, {"myproject": "FROM-PERSISTENT"})
    local = projects_dir / "myconfig" / "local.toml"
    _write_projects_registry(local, {"myproject": "FROM-LOCAL"})
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: local)
    monkeypatch.setattr(
        "ai_cli.config.load_config",
        lambda: {"project_registry": {str(repo): {"prefix": "FROM-XDG", "type": "tool"}}},
    )

    assert resolve_project_prefix(repo) == "FROM-PERSISTENT"


def test_given_no_persistent_registry_when_resolving_then_the_main_project_registry_answers(fleet, monkeypatch):
    projects_dir, registry = fleet
    local = projects_dir / "myconfig" / "local.toml"
    _write_projects_registry(local, {"myproject": "FROM-LOCAL"})
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: local)

    assert resolve_project_prefix(projects_dir / "myproject") == "FROM-LOCAL"


def test_given_only_the_config_table_when_resolving_then_it_answers(fleet, monkeypatch):
    projects_dir, _registry = fleet
    repo = projects_dir / "myproject"
    monkeypatch.setattr(
        "ai_cli.config.load_config",
        lambda: {"project_registry": {str(repo): {"prefix": "FROM-XDG", "type": "tool"}}},
    )

    assert resolve_project_prefix(repo) == "FROM-XDG"


def test_given_an_explicitly_configured_registry_path_when_resolving_then_it_is_used(fleet, monkeypatch, tmp_path):
    """``[project] fleet_registry`` overrides discovery, including its location."""
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "FROM-DISCOVERED"})
    explicit = tmp_path / "elsewhere" / "registry.toml"
    _write_projects_registry(explicit, {"myproject": "FROM-EXPLICIT"})
    monkeypatch.setattr(
        "ai_cli.config.load_config",
        lambda: {"project": {"fleet_registry": str(explicit)}},
    )

    assert get_fleet_registry_path() == explicit
    assert resolve_project_prefix(projects_dir / "myproject") == "FROM-EXPLICIT"


# --- AC-6: every repository resolves from scratch, with no [project_registry] ---


def test_given_a_config_with_no_project_registry_section_when_resolving_then_every_listed_repo_resolves(fleet):
    """AC-6: a freshly provisioned machine resolves every repository out of the box.

    The ``load_config`` stub returns ``{}`` -- literally no ``[project_registry]``
    section -- so any answer here comes from the persistent registry alone.
    """
    projects_dir, registry = fleet
    expected = {name: name.upper() for name in _PROJECTS}
    _write_projects_registry(registry, expected)

    resolved = {name: resolve_project_prefix(projects_dir / name) for name in _PROJECTS}

    assert resolved == expected


def test_given_a_repo_absent_from_every_tier_when_resolving_without_a_terminal_then_it_still_fails(fleet):
    """Positive control for the AC-6 case: resolution can still come out negative.

    Without this, the from-scratch test above would pass against an implementation
    that returned a prefix for any name whatsoever.
    """
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})

    with patch("ai_cli.config._can_prompt", return_value=False):
        with pytest.raises(ProjectPrefixError, match="ai register"):
            resolve_project_prefix(projects_dir / "myapp")


# --- AC-4: worktree paths resolve to their owner and are not registered ---


@pytest.mark.parametrize("worktree_parent", [".worktrees", ".claude/worktrees"])
def test_given_a_path_inside_a_worktree_when_resolving_then_the_owning_repo_prefix_is_returned(fleet, worktree_parent):
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})
    worktree = projects_dir / "myproject" / worktree_parent / "session-1"
    worktree.mkdir(parents=True)

    assert resolve_project_prefix(worktree) == "MYPROJECT"


@pytest.mark.parametrize("worktree_parent", [".worktrees", ".claude/worktrees"])
def test_given_a_worktree_path_when_resolving_then_no_registry_entry_is_written_for_it(fleet, worktree_parent):
    """AC-4's second half: resolution must not key an entry on the worktree path.

    Writing one is what filled the real config with per-agent-worktree junk.
    """
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})
    worktree = projects_dir / "myproject" / worktree_parent / "session-1"
    worktree.mkdir(parents=True)

    resolve_project_prefix(worktree)

    config_file = Path(get_fleet_registry_path()).parent.parent / "config.toml"
    assert not config_file.exists()
    assert "session-1" not in registry.read_text(encoding="utf-8")


def test_given_a_worktree_of_a_self_describing_repo_when_resolving_then_the_owner_is_registered(fleet):
    """The ``[tool.ai-cli]`` tier must key its write on the owner, not the worktree."""
    import tomllib

    from ai_cli.config import get_xdg_config_home

    projects_dir, _registry = fleet
    repo = projects_dir / "myproject"
    (repo / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "MYPROJECT"\n', encoding="utf-8")
    worktree = repo / ".claude" / "worktrees" / "agent-1"
    worktree.mkdir(parents=True)

    assert resolve_project_prefix(worktree) == "MYPROJECT"

    with (get_xdg_config_home() / "config.toml").open("rb") as handle:
        table = tomllib.load(handle)["project_registry"]
    assert list(table) == [str(repo)]


# --- AC-5: an entry naming a directory that is not there is reported ---


def test_given_a_registry_entry_whose_repo_directory_is_absent_when_resolving_then_it_is_reported(fleet):
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"deleted-project": "GONE"})

    with pytest.raises(ProjectPrefixError, match="does not exist"):
        resolve_project_prefix(projects_dir / "deleted-project")


def test_given_a_registry_entry_whose_repo_directory_is_absent_when_resolving_then_no_prefix_is_returned(fleet):
    """The report must replace the answer, not accompany it.

    A resolution that both warns and returns "GONE" would satisfy a message-only
    assertion while still minting session names against a repository that is not
    on disk.
    """
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"deleted-project": "GONE"})

    with pytest.raises(ProjectPrefixError) as exc:
        resolve_project_prefix(projects_dir / "deleted-project")
    assert str(projects_dir / "deleted-project") in str(exc.value)


# --- AC-3 + the interactivity trap: prompt with a terminal, fail loudly without ---


def test_given_no_registry_at_any_tier_and_a_terminal_when_resolving_then_it_prompts_and_persists(fleet):
    projects_dir, registry = fleet
    registry.unlink(missing_ok=True)
    repo = projects_dir / "myproject"

    with (
        patch("ai_cli.config._can_prompt", return_value=True),
        patch("builtins.input", return_value="MYPROJECT") as prompt,
    ):
        assert resolve_project_prefix(repo) == "MYPROJECT"

    prompt.assert_called_once()
    # Persisted, so the next resolution needs no prompt at all.
    assert resolve_project_prefix(repo) == "MYPROJECT"


def test_given_a_configured_registry_path_that_does_not_exist_when_prompted_then_the_file_is_created(
    fleet, monkeypatch, tmp_path
):
    """AC-3: create the file at the first writable tier, rather than erroring.

    A configured path names the intended location even before anything is there,
    so the persistent tier is writable and the prompt's answer lands in it.
    """
    projects_dir, registry = fleet
    registry.unlink(missing_ok=True)
    target = tmp_path / "new-config-repo" / "config" / "fleet-projects.toml"
    monkeypatch.setattr(
        "ai_cli.config.load_config",
        lambda: {"project": {"fleet_registry": str(target)}},
    )
    assert not target.exists()

    with (
        patch("ai_cli.config._can_prompt", return_value=True),
        patch("builtins.input", return_value="MYPROJECT"),
    ):
        assert resolve_project_prefix(projects_dir / "myproject") == "MYPROJECT"

    assert target.is_file()
    assert 'task_prefix = "MYPROJECT"' in target.read_text(encoding="utf-8")


def test_given_no_registry_location_is_known_when_prompted_then_the_user_config_is_created_and_used(fleet):
    """With no persistent registry configured or discoverable, the user config is the tier.

    Nothing here can invent which repository should hold a fleet-wide registry, and
    guessing one would write into a repository the user never nominated. The user's
    own config always exists or can be created, which is what lets create-and-prompt
    complete instead of dead-ending.
    """
    import tomllib

    from ai_cli.config import get_xdg_config_home

    projects_dir, registry = fleet
    registry.unlink(missing_ok=True)
    config_file = get_xdg_config_home() / "config.toml"
    assert not config_file.exists()

    with (
        patch("ai_cli.config._can_prompt", return_value=True),
        patch("builtins.input", return_value="MYPROJECT"),
    ):
        assert resolve_project_prefix(projects_dir / "myproject") == "MYPROJECT"

    assert config_file.is_file()
    with config_file.open("rb") as handle:
        assert tomllib.load(handle)["project_registry"][str(projects_dir / "myproject")]["prefix"] == "MYPROJECT"


def test_given_an_existing_persistent_registry_when_prompted_then_the_answer_lands_in_it(fleet):
    """A discoverable registry is the first writable tier, so the answer goes there."""
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myapp": "MYAPP"})

    with (
        patch("ai_cli.config._can_prompt", return_value=True),
        patch("builtins.input", return_value="MYPROJECT"),
    ):
        assert resolve_project_prefix(projects_dir / "myproject") == "MYPROJECT"

    text = registry.read_text(encoding="utf-8")
    assert 'task_prefix = "MYPROJECT"' in text
    # The pre-existing entry must survive: appending is not rewriting.
    assert 'task_prefix = "MYAPP"' in text


def test_given_no_terminal_when_resolving_an_unregistered_repo_then_it_fails_loudly_without_prompting(fleet):
    """The interactivity trap: a prompt with no TTY hangs the caller forever.

    ``ai c`` runs from git hooks, agent shells and ``ai update``. ``input`` is
    patched to raise so a regression that prompts anyway fails as a call to a
    forbidden boundary rather than blocking the suite until it times out.
    """
    projects_dir, registry = fleet
    registry.unlink(missing_ok=True)

    def _must_not_prompt(*_args, **_kwargs):
        raise AssertionError("resolution prompted with no terminal attached")

    with (
        patch("ai_cli.config._can_prompt", return_value=False),
        patch("builtins.input", _must_not_prompt),
    ):
        with pytest.raises(ProjectPrefixError, match=r"ai register -p .* -x PREFIX"):
            resolve_project_prefix(projects_dir / "myproject")


def test_given_an_empty_answer_at_the_prompt_when_resolving_then_it_fails_with_the_remedy(fleet):
    projects_dir, registry = fleet
    registry.unlink(missing_ok=True)

    with (
        patch("ai_cli.config._can_prompt", return_value=True),
        patch("builtins.input", return_value="  "),
    ):
        with pytest.raises(ProjectPrefixError, match="ai register"):
            resolve_project_prefix(projects_dir / "myproject")


def test_given_a_closed_stdin_when_asking_whether_to_prompt_then_the_answer_is_no(fleet):
    """A replaced or closed stdin is not a terminal and must not raise."""
    from ai_cli.config import _can_prompt

    class _Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    with patch("sys.stdin", _Closed()):
        assert _can_prompt() is False


# --- AC-8: stale config entries are ignored rather than fatal ---


def test_given_stale_worktree_and_duplicate_keys_in_the_config_table_when_resolving_then_it_still_works(
    fleet, monkeypatch
):
    """AC-8: keys that collapse onto one root are redundant, not ambiguous.

    Reproduces the real config's shape -- two agent worktree paths, a throwaway
    probe directory, and the repository root itself, all carrying one prefix.
    """
    projects_dir, _registry = fleet
    repo = projects_dir / "myproject"
    (repo / ".git").mkdir()
    table = {
        str(repo / ".claude" / "worktrees" / "agent-aaaa"): {"prefix": "MYPROJECT", "type": "tool"},
        str(repo): {"prefix": "MYPROJECT", "type": "tool"},
        str(projects_dir / "throwaway-probe"): {"prefix": "MYPROJECT", "type": "tool"},
        str(repo / ".claude" / "worktrees" / "agent-bbbb"): {"prefix": "MYPROJECT", "type": "tool"},
    }
    monkeypatch.setattr("ai_cli.config.load_config", lambda: {"project_registry": table})

    assert resolve_project_prefix(repo) == "MYPROJECT"


def test_given_two_config_keys_for_one_root_that_disagree_when_resolving_then_it_is_reported(fleet, monkeypatch):
    """Collapsing duplicates must not swallow a real contradiction.

    Without this, the tolerance added for AC-8 would silently pick whichever
    conflicting prefix happened to be read last.
    """
    projects_dir, _registry = fleet
    repo = projects_dir / "myproject"
    (repo / ".git").mkdir()
    table = {
        str(repo): {"prefix": "ONE", "type": "tool"},
        str(repo / ".worktrees" / "session-1"): {"prefix": "TWO", "type": "tool"},
    }
    monkeypatch.setattr("ai_cli.config.load_config", lambda: {"project_registry": table})

    with pytest.raises(ProjectPrefixError, match="Ambiguous"):
        resolve_project_prefix(repo)


# --- Registry validation: a malformed persistent registry must not be guessed past ---


@pytest.mark.parametrize(
    "content",
    [
        '[[projects]]\nname = "myproject"\n',
        '[[projects]]\ntask_prefix = "MYPROJECT"\n',
        '[[projects]]\nname = "myproject"\ntask_prefix = ""\n',
        '[[projects]]\nname = "myproject"\ntask_prefix = "A"\n\n[[projects]]\nname = "myproject"\ntask_prefix = "B"\n',
        '[[projects]]\nname = "myproject"\ntask_prefix = "A"\n\n[[projects]]\nname = "myapp"\ntask_prefix = "A"\n',
        "not valid toml {{{",
    ],
)
def test_given_a_malformed_persistent_registry_when_resolving_then_it_is_reported(fleet, content):
    projects_dir, registry = fleet
    registry.write_text(content, encoding="utf-8")

    with pytest.raises(ProjectPrefixError):
        resolve_project_prefix(projects_dir / "myproject")


def test_given_two_persistent_registries_when_locating_one_then_the_ambiguity_is_reported(fleet):
    """Discovery by shape must not silently pick one of two candidates."""
    projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})
    _write_projects_registry(projects_dir / "myapp" / FLEET_REGISTRY_MARKER, {"myapp": "MYAPP"})

    with pytest.raises(ProjectPrefixError, match="Multiple persistent project registries"):
        get_fleet_registry_path()


# --- resolve_project_prefix_by_name reads the same tiers ---


def test_given_a_repo_in_the_persistent_registry_when_resolving_by_name_then_it_returns_that_prefix(fleet):
    _projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})

    assert resolve_project_prefix_by_name("myproject") == "MYPROJECT"


def test_given_a_name_absent_from_every_tier_when_resolving_by_name_without_a_terminal_then_it_fails(fleet):
    _projects_dir, registry = fleet
    _write_projects_registry(registry, {"myproject": "MYPROJECT"})

    with patch("ai_cli.config._can_prompt", return_value=False):
        with pytest.raises(ProjectPrefixError, match="ai register"):
            resolve_project_prefix_by_name("myapp")


def test_given_only_worktree_keys_for_a_name_in_the_config_table_when_resolving_by_name_then_it_resolves(
    fleet, monkeypatch
):
    """Name lookup must not see one repository as several just because of worktrees."""
    projects_dir, _registry = fleet
    repo = projects_dir / "myproject"
    (repo / ".git").mkdir()
    table = {
        str(repo / ".worktrees" / "session-1"): {"prefix": "MYPROJECT", "type": "tool"},
        str(repo / ".claude" / "worktrees" / "agent-1"): {"prefix": "MYPROJECT", "type": "tool"},
    }
    monkeypatch.setattr("ai_cli.config.load_config", lambda: {"project_registry": table})

    assert resolve_project_prefix_by_name("myproject") == "MYPROJECT"


# --- The registered-prefix path still writes to config.toml, unchanged ---


def test_given_ai_register_when_run_for_a_repo_then_resolution_returns_that_prefix(fleet):
    """Parity: the explicit ``ai register`` path keeps working across the change."""
    projects_dir, registry = fleet
    registry.unlink(missing_ok=True)
    repo = projects_dir / "myproject"
    (repo / ".git").mkdir()

    register_project(repo, "MYPROJECT", "tool")

    assert resolve_project_prefix(repo) == "MYPROJECT"
