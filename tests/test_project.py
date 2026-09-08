import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.main import (
    _find_aicli_project_path,
    _find_project_dir,
    _get_main_project_dir,
    _get_main_project_name,
    _get_project_prefix_by_name,
    _get_project_registry_path,
    _get_projects_dir,
    get_current_project_name,
    get_project_aliases,
    get_project_prefix,
    load_project_registry,
    validate_registry_completeness,
)

# --- _find_project_dir tests ---


def test_find_project_dir_when_lowercase_projects_exists_then_returns_it():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        expected = home / "projects" / "myapp"
        expected.mkdir(parents=True)
        assert _find_project_dir("myapp", _home=home) == expected


def test_find_project_dir_when_only_uppercase_Projects_exists_then_returns_lowercase():
    """Function always returns lowercase projects/ path regardless of what exists on disk.

    Not actually platform-dependent: _find_project_dir() is a pure path-join with no
    filesystem probing (true since the initial commit), so this holds identically on
    case-insensitive (macOS/Windows) and case-sensitive (Linux) filesystems alike.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / "Projects" / "myapp").mkdir(parents=True)
        assert _find_project_dir("myapp", _home=home) == home / "projects" / "myapp"


def test_find_project_dir_when_lowercase_takes_priority_over_uppercase():
    """Both dirs "exist" is irrelevant to the pure-path-join function under test, but
    on a case-insensitive filesystem (macOS/Windows) `projects/` and `Projects/` are
    the same on-disk directory — exist_ok=True keeps the setup portable instead of
    skipping the test outright on those platforms.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / "projects" / "myapp").mkdir(parents=True)
        (home / "Projects" / "myapp").mkdir(parents=True, exist_ok=True)
        assert _find_project_dir("myapp", _home=home) == home / "projects" / "myapp"


def test_find_project_dir_when_not_found_then_returns_lowercase_default():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        result = _find_project_dir("nonexistent", _home=home)
        assert result == home / "projects" / "nonexistent"


# --- get_current_project_name tests ---


def test_get_current_project_name_when_in_normal_dir_then_returns_dir_name():
    with patch("pathlib.Path.cwd", return_value=Path("/home/user/projects/aurion")):
        assert get_current_project_name() == "aurion"


def test_get_current_project_name_when_in_worktree_then_returns_project_name():
    with patch("pathlib.Path.cwd", return_value=Path("/home/user/projects/myproject/.worktrees/sw-2")):
        assert get_current_project_name() == "myproject"


def test_get_current_project_name_when_worktree_nested_then_returns_project_name():
    with patch("pathlib.Path.cwd", return_value=Path("/home/user/projects/myapp/.worktrees/feature-1")):
        assert get_current_project_name() == "myapp"


# --- Project helpers ---


class TestProjectHelpers:
    def test_get_projects_dir_when_custom_configured_then_uses_it(self):
        with patch("ai_cli.config.load_config", return_value={"project": {"projects_dir": "/tmp/custom"}}):
            result = _get_projects_dir()
        assert result == Path("/tmp/custom")

    def test_find_project_dir_when_no_home_arg_then_uses_projects_dir(self):
        with patch("ai_cli.config._get_projects_dir", return_value=Path("/srv/projects")):
            result = _find_project_dir("foo")
        assert result == Path("/srv/projects/foo")

    def test_get_main_project_name_when_exception_then_returns_none(self):
        with patch("ai_cli.config.load_config", side_effect=RuntimeError("broken")):
            result = _get_main_project_name()
        assert result is None

    def test_get_main_project_dir_when_name_configured_then_returns_path(self):
        with patch("ai_cli.config._get_main_project_name", return_value="myproject"):
            with patch("ai_cli.config._find_project_dir", return_value=Path("/home/u/projects/myproject")):
                result = _get_main_project_dir()
        assert result == Path("/home/u/projects/myproject")

    def test_get_project_registry_path_when_toml_exists_then_returns_it(self, tmp_path):
        # Registry file is named {project_name}.toml, not a fixed "registry.toml"
        toml_file = tmp_path / "myproject.toml"
        toml_file.write_text("[projects]\n")
        with patch("ai_cli.config._get_main_project_dir", return_value=tmp_path):
            with patch("ai_cli.config._get_main_project_name", return_value="myproject"):
                result = _get_project_registry_path()
        assert result == toml_file

    def test_get_project_prefix_by_name_when_found_then_returns_prefix(self, tmp_path):
        toml_file = tmp_path / "registry.toml"
        toml_content = b'[[projects]]\nname = "myapp"\ntask_prefix = "MA"\n'
        toml_file.write_bytes(toml_content)
        with patch("ai_cli.config._get_project_registry_path", return_value=toml_file):
            result = _get_project_prefix_by_name("myapp")
        assert result == "MA"

    def test_get_project_prefix_by_name_when_not_found_then_raises_registration_remedy(self):
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            with pytest.raises(ValueError, match="ai register"):
                _get_project_prefix_by_name("myproject")

    def test_get_project_prefix_by_name_when_override_exists_then_uses_override(self):
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            with patch("ai_cli.config.load_config", return_value={"project_prefixes": {"myapp-long-name": "mln"}}):
                result = _get_project_prefix_by_name("myapp-long-name")
        assert result == "mln"

    def test_get_project_prefix_by_name_when_override_takes_precedence_over_registry(self, tmp_path):
        toml_file = tmp_path / "registry.toml"
        toml_content = b'[[projects]]\nname = "myapp-long-name"\ntask_prefix = "WRONG"\n'
        toml_file.write_bytes(toml_content)
        with patch("ai_cli.config._get_project_registry_path", return_value=toml_file):
            with patch("ai_cli.config.load_config", return_value={"project_prefixes": {"myapp-long-name": "mln"}}):
                result = _get_project_prefix_by_name("myapp-long-name")
        assert result == "mln"

    def test_get_project_prefix_by_name_when_no_override_configured_then_raises_registration_remedy(self):
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            with patch("ai_cli.config.load_config", return_value={}):
                with pytest.raises(ValueError, match="ai register"):
                    _get_project_prefix_by_name("myapp-long-name")

    def test_get_project_prefix_by_name_when_override_is_longer_than_three_chars_then_kept(self):
        """Overrides are used verbatim — they are not re-truncated to 3 chars."""
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            with patch("ai_cli.config.load_config", return_value={"project_prefixes": {"myservice": "msvc"}}):
                result = _get_project_prefix_by_name("myservice")
        assert result == "msvc"

    def test_get_project_prefix_by_name_when_project_not_in_override_map_then_raises_registration_remedy(self):
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            with patch("ai_cli.config.load_config", return_value={"project_prefixes": {"other": "oth"}}):
                with pytest.raises(ValueError, match="ai register"):
                    _get_project_prefix_by_name("myproject-two")

    def test_get_project_aliases_when_registry_exists_then_builds_map(self, tmp_path):
        toml_file = tmp_path / "registry.toml"
        toml_content = b'[[projects]]\nname = "myproject"\ntask_prefix = "MP"\n\n[[projects]]\nname = "ai-dojo"\ntask_prefix = "AD"\n'
        toml_file.write_bytes(toml_content)
        with patch("ai_cli.config._get_project_registry_path", return_value=toml_file):
            result = get_project_aliases()
        assert result["mp"] == "myproject"
        assert result["ad"] == "ai-dojo"

    def test_get_project_aliases_when_no_registry_then_empty(self):
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            result = get_project_aliases()
        assert result == {}


# --- _find_aicli_project_path ---


class TestFindAicliProjectPath:
    def test_when_config_has_project_path_then_returns_it(self, tmp_path):
        result = _find_aicli_project_path({"deploy": {"project_path": str(tmp_path)}})
        assert result == tmp_path

    def test_when_no_config_then_detects_via_package_file(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('name = "ai-cli-utils"\nversion = "0.1.0"\n')
        fake_origin = str(tmp_path / "ai_cli" / "main.py")
        import importlib.util as _ilu

        mock_spec = MagicMock()
        mock_spec.origin = fake_origin
        with patch.object(_ilu, "find_spec", return_value=mock_spec):
            result = _find_aicli_project_path({})
        assert result == tmp_path

    def test_when_in_project_dir_then_cwd_fallback_works(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('name = "ai-cli-utils"\nversion = "0.1.0"\n')
        import importlib.util as _ilu

        with (
            patch.object(_ilu, "find_spec", return_value=None),
            patch("ai_cli.main.Path.cwd", return_value=tmp_path),
        ):
            result = _find_aicli_project_path({})
        assert result == tmp_path

    def test_when_in_wrong_dir_then_cwd_fallback_skipped(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('name = "other-project"\nversion = "0.1.0"\ndependencies = ["ai-cli-utils"]\n')
        import importlib.util as _ilu

        with (
            patch.object(_ilu, "find_spec", return_value=None),
            patch("ai_cli.main.Path.cwd", return_value=tmp_path),
        ):
            result = _find_aicli_project_path({})
        assert result is None

    def test_when_package_not_found_and_no_cwd_match_then_returns_none(self):
        import importlib.util as _ilu

        with (
            patch.object(_ilu, "find_spec", return_value=None),
            patch("ai_cli.main.Path.cwd", return_value=Path("/nonexistent/path")),
        ):
            result = _find_aicli_project_path({})
        assert result is None


# --- load_project_registry ---


class TestLoadProjectRegistry:
    def test_load_project_registry_when_valid_then_returns_projects(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            result = load_project_registry(_force=True)
        assert len(result) == 1
        assert result[0]["name"] == "app"

    def test_load_project_registry_when_cached_then_returns_same(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            r1 = load_project_registry(_force=True)
            r2 = load_project_registry()
        assert r1 is r2

    def test_load_project_registry_when_no_registry_then_returns_empty(self):
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            result = load_project_registry(_force=True)
        assert result == []

    def test_load_project_registry_when_missing_name_then_exits(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\ntask_prefix = "APP"\n')
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_missing_task_prefix_then_exits(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\n')
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_duplicate_name_then_exits(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(
            b'[[projects]]\nname = "app"\ntask_prefix = "A1"\n[[projects]]\nname = "app"\ntask_prefix = "A2"\n'
        )
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_duplicate_prefix_then_exits(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(
            b'[[projects]]\nname = "app1"\ntask_prefix = "AP"\n[[projects]]\nname = "app2"\ntask_prefix = "AP"\n'
        )
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_toml_parse_error_then_returns_empty(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b"not valid toml {{{{")
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            result = load_project_registry(_force=True)
        assert result == []


# --- validate_registry_completeness ---


class TestValidateRegistryCompleteness:
    def test_validate_when_no_registry_then_returns_true(self):
        with patch("ai_cli.config._get_project_registry_path", return_value=None):
            assert validate_registry_completeness(interactive=False) is True

    def test_validate_when_all_registered_then_returns_true(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
        ):
            assert validate_registry_completeness(interactive=False) is True

    def test_validate_when_unregistered_noninteractive_then_returns_false(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
        ):
            assert validate_registry_completeness(interactive=False) is False

    def test_validate_when_user_registers_then_returns_true(self, tmp_path, monkeypatch):
        import tomllib

        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        home = tmp_path / "home"
        projects_dir = home / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", return_value="y"),
        ):
            assert validate_registry_completeness(interactive=True) is True
        with registry.open("rb") as handle:
            new_project = tomllib.load(handle)["projects"][1]
        assert new_project["name"] == "newapp"
        assert new_project["path"] == "~/projects/newapp"

    def test_validate_when_user_declines_then_returns_false(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", return_value="n"),
        ):
            assert validate_registry_completeness(interactive=True) is False

    def test_validate_when_eof_then_returns_false(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", side_effect=EOFError),
        ):
            assert validate_registry_completeness(interactive=True) is False

    def test_validate_when_interrupted_then_preserves_registry_and_propagates_interrupt(self, tmp_path):
        registry = tmp_path / "registry.toml"
        original = b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n'
        registry.write_bytes(original)
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        (projects_dir / "anotherapp").mkdir(parents=True)
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", side_effect=["y", KeyboardInterrupt]),
            pytest.raises(KeyboardInterrupt),
        ):
            validate_registry_completeness(interactive=True)
        assert registry.read_bytes() == original

    def test_validate_skips_hidden_dirs(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / ".hidden").mkdir(parents=True)
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
        ):
            assert validate_registry_completeness(interactive=False) is True

    def test_validate_when_custom_prefix_then_uses_it(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", return_value="CUSTOM"),
        ):
            assert validate_registry_completeness(interactive=True) is True
        content = registry.read_text()
        assert "CUSTOM" in content

    def test_validate_when_projects_dir_missing_then_returns_true(self, tmp_path):
        registry = tmp_path / "registry.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        nonexistent = tmp_path / "nonexistent"
        with (
            patch("ai_cli.config._get_project_registry_path", return_value=registry),
            patch("ai_cli.config._get_projects_dir", return_value=nonexistent),
        ):
            assert validate_registry_completeness(interactive=False) is True


# --- get_project_prefix — project found in registry ---


class TestGetProjectPrefixRegistryMatch:
    def test_get_project_prefix_when_project_matches_registry_then_returns_prefix(self, tmp_path):
        with patch("ai_cli.session.resolve_project_prefix", return_value="PROJECT"):
            assert get_project_prefix() == "PROJECT"


# --- Project path separator validation ---


class TestProjectPathSeparatorValidation:
    """Tests for --project path separator guard."""

    def test_when_project_contains_slash_then_exits_1(self, capsys):
        with patch("sys.argv", ["ai", "c", "1", "-R", "--project", "../evil"]):
            with patch("ai_cli.config.load_config", return_value={"remote": {"host": "h", "user": "u"}}):
                with patch("ai_cli.config.get_project_aliases", return_value={}):
                    with patch("ai_cli.config.get_current_project_name", return_value=""):
                        from ai_cli.main import cli

                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 1
        assert "path separator" in capsys.readouterr().err

    def test_when_project_has_no_separator_then_proceeds(self, capsys):
        async def fake_transport_loop(*args, **kwargs):
            pass

        with patch("sys.argv", ["ai", "c", "1", "-R", "--project", "myproject"]):
            with patch("ai_cli.config.load_config", return_value={"remote": {"host": "h", "user": "u"}}):
                with patch("ai_cli.config.get_project_aliases", return_value={}):
                    with patch("ai_cli.config.get_current_project_name", return_value=""):
                        with patch("ai_cli.config.resolve_project_prefix_by_name", return_value="my"):
                            with patch("ai_cli.transport._run_transport_loop", side_effect=fake_transport_loop):
                                with patch("ai_cli.transport._ensure_vpn_watcher"):
                                    with patch("ai_cli.transport._maybe_stop_vpn_watcher"):
                                        from ai_cli.main import cli

                                        with pytest.raises(SystemExit) as exc:
                                            cli()
        assert exc.value.code == 0


# --- Coverage gap tests: project registry / alias error branches ---


class TestProjectRegistryExceptionBranches:
    def test_get_project_prefix_by_name_when_registry_read_fails_then_raises_registration_remedy(self, tmp_path):
        registry = tmp_path / "broken.toml"
        registry.write_text("not valid toml {{{")
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            with pytest.raises(ValueError, match="ai register"):
                _get_project_prefix_by_name("myproject")

    def test_get_project_aliases_when_registry_read_fails_then_returns_empty(self, tmp_path):
        """Covers lines 216-217: exception in get_project_aliases."""
        registry = tmp_path / "broken.toml"
        registry.write_text("not valid toml {{{")
        with patch("ai_cli.config._get_project_registry_path", return_value=registry):
            result = get_project_aliases()
        assert result == {}

    def test_get_project_prefix_when_repo_unregistered_then_raises_registration_remedy(self, tmp_path, monkeypatch):
        """An unregistered repository must name the remedy, never invent a prefix.

        AI-CLI-192: this replaces a test that corrupted the *legacy* name-keyed
        registry TOML and expected that to break prefix resolution. Since AI-CLI-160
        made the config-backed ``[project_registry]`` the single source of truth,
        that file no longer participates: ``resolve_project_prefix`` consults the
        config table and then ``pyproject.toml``, so the old test asserted a
        dependency the code deliberately no longer has.

        The contract asserted here is the one AI-CLI-160 actually promises and is
        the reason the original test existed -- an unresolvable prefix fails loudly
        with an actionable remedy rather than silently degrading to a truncated,
        default, or empty prefix. ``tmp_path`` is registered nowhere and carries no
        ``pyproject.toml``, so resolution genuinely has no answer.
        """
        monkeypatch.chdir(tmp_path)
        with patch("ai_cli.config.load_config", return_value={}):
            with pytest.raises(ValueError, match="ai register") as exc:
                get_project_prefix()

        # The remedy must be actionable: it names the flags, not just the tool.
        message = str(exc.value)
        assert "-x PREFIX" in message
        # And it must not have quietly produced a prefix from the directory name.
        assert tmp_path.name[:3] not in message.replace(str(tmp_path), "")

    def test_get_project_prefix_when_registry_table_is_corrupt_then_raises_remedy(self, tmp_path, monkeypatch):
        """A corrupt ``[project_registry]`` must raise, never yield a wrong prefix.

        The failure mode that matters (AI-CLI-192 AC-2): if a malformed registry
        silently degraded to a default, display ids could be minted against the
        wrong project. Each case below is a distinct way the table can be wrong.
        """
        monkeypatch.chdir(tmp_path)
        corrupt_tables = [
            "not-a-table",
            {str(tmp_path): "not-an-entry"},
            {str(tmp_path): {"prefix": ""}},
            {str(tmp_path): {"type": "tool"}},
        ]
        for table in corrupt_tables:
            with patch("ai_cli.config.load_config", return_value={"project_registry": table}):
                with pytest.raises(ValueError, match="ai register"):
                    get_project_prefix()

    def test_get_project_prefix_when_registered_then_returns_that_prefix(self, tmp_path, monkeypatch):
        """Positive control: the same call really can succeed.

        Without this, the two tests above would pass equally well against a
        function that raised unconditionally.
        """
        monkeypatch.chdir(tmp_path)
        table = {str(tmp_path): {"prefix": "MYPROJECT", "type": "tool"}}
        with patch("ai_cli.config.load_config", return_value={"project_registry": table}):
            assert get_project_prefix() == "MYPROJECT"


class TestProjectsDirEdgeCases:
    def test_get_projects_dir_when_exception_then_returns_default(self):
        with patch("ai_cli.config.load_config", side_effect=RuntimeError("broken")):
            result = _get_projects_dir()
        assert result == Path.home() / "projects"

    def test_get_project_registry_path_when_no_main_dir_then_returns_none(self):
        with patch("ai_cli.config._get_main_project_dir", return_value=None):
            result = _get_project_registry_path()
        assert result is None

    def test_get_main_project_dir_when_not_configured_then_returns_none(self):
        with patch("ai_cli.config._get_main_project_name", return_value=None):
            result = _get_main_project_dir()
        assert result is None
