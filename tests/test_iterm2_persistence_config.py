"""The shipped iTerm2 defaults and the ``[iterm2.persistence]`` loader."""

import tomllib
from pathlib import Path

import pytest

from ai_cli.iterm2 import _DEFAULT_ITERM2_CONFIG, PersistenceConfigError, load_persistence_config

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: The documented defaults (docs/iterm2-persistence.md), written out rather than
#: imported so a change to the code's defaults cannot silently change the expectation.
_DOCUMENTED_DEFAULTS = {
    "enabled": True,
    "tracking": {
        "enabled": True,
        "include_local": True,
        "include_remote": True,
        "include": [],
        "exclude": [],
        "refresh_on_launch": True,
    },
    "restore": {
        "enabled": False,
        "on_startup": False,
        "on_demand": True,
        "mode": "arrangement+sessions",
        "default_arrangement": "",
        "use_it2": True,
        "fill_arrangement": True,
        "include_local": True,
        "include_remote": True,
        "include": [],
        "exclude": [],
        "remote_hosts": [],
        "max_sessions": 0,
        "stagger_seconds": 1.0,
        "confirm": False,
        "relaunch": {
            "on_terminal_reopen": True,
            "scenarios": {"host_reboot": True, "terminal_quit_or_crash": True, "manual_exit": False},
        },
    },
}


@pytest.fixture
def iterm2_toml(tmp_path, monkeypatch):
    """The iterm2.toml the loader reads, under a per-test config dir.

    Patches the loader's directory source rather than ``XDG_CONFIG_HOME``, which
    ``get_xdg_config_home`` ignores on Windows in favour of ``APPDATA``.
    """
    config_dir = tmp_path / "config" / "ai-cli-utils"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr("ai_cli.iterm2.get_xdg_config_home", lambda: config_dir)
    return config_dir / "iterm2.toml"


def test_given_shipped_default_config_when_reference_copy_read_then_it_is_identical():
    """docs/reference/iterm2-defaults.toml documents the file `ai` writes on first use."""
    reference = (_REPO_ROOT / "docs" / "reference" / "iterm2-defaults.toml").read_text(encoding="utf-8")

    assert reference == _DEFAULT_ITERM2_CONFIG


def test_given_toml_without_persistence_section_when_loaded_then_every_key_is_its_documented_default(iterm2_toml):
    iterm2_toml.write_text("[iterm2]\nenabled = true\n", encoding="utf-8")

    assert load_persistence_config() == _DOCUMENTED_DEFAULTS


def _uncommented(text: str) -> str:
    """The shipped text with every single-``#`` template line turned live."""
    return "\n".join(line[2:] if line.startswith("# ") else line for line in text.splitlines())


def test_given_shipped_default_config_when_parsed_then_it_defines_no_persistence_table():
    """An installer writes [iterm2.persistence] in its own block; TOML forbids a second definition."""
    shipped = tomllib.loads(_DEFAULT_ITERM2_CONFIG)

    assert "persistence" not in shipped["iterm2"]


def test_given_no_toml_when_loaded_then_the_shipped_file_yields_the_documented_defaults(iterm2_toml):
    """First use writes the shipped file; its commented persistence template must equal the code defaults."""
    assert not iterm2_toml.exists()

    assert load_persistence_config() == _DOCUMENTED_DEFAULTS
    template = tomllib.loads(_uncommented(iterm2_toml.read_text(encoding="utf-8")))
    assert template["iterm2"]["persistence"] == _DOCUMENTED_DEFAULTS


def test_given_partial_section_when_loaded_then_set_keys_win_and_the_rest_default(iterm2_toml):
    iterm2_toml.write_text(
        '[iterm2.persistence.restore]\nenabled = true\ndefault_arrangement = "main"\nstagger_seconds = 2\n',
        encoding="utf-8",
    )

    restore = load_persistence_config()["restore"]

    assert restore["enabled"] is True
    assert restore["default_arrangement"] == "main"
    assert restore["stagger_seconds"] == 2
    assert restore["on_startup"] is False


def test_given_restore_table_without_relaunch_table_when_loaded_then_relaunch_takes_the_code_defaults(iterm2_toml):
    iterm2_toml.write_text("[iterm2.persistence.restore]\nenabled = true\n", encoding="utf-8")

    relaunch = load_persistence_config()["restore"]["relaunch"]

    assert relaunch == {
        "on_terminal_reopen": True,
        "scenarios": {"host_reboot": True, "terminal_quit_or_crash": True, "manual_exit": False},
    }


def test_given_one_relaunch_scenario_set_when_loaded_then_it_wins_and_the_others_default(iterm2_toml):
    iterm2_toml.write_text(
        "[iterm2.persistence.restore.relaunch]\non_terminal_reopen = false\n"
        "[iterm2.persistence.restore.relaunch.scenarios]\nmanual_exit = true\n",
        encoding="utf-8",
    )

    relaunch = load_persistence_config()["restore"]["relaunch"]

    assert relaunch["on_terminal_reopen"] is False
    assert relaunch["scenarios"] == {"host_reboot": True, "terminal_quit_or_crash": True, "manual_exit": True}


@pytest.mark.parametrize(
    ("toml_text", "message"),
    [
        pytest.param(
            '[iterm2.persistence.restore.relaunch.scenarios]\nhost_reboot = "yes"\n',
            "[iterm2.persistence.restore.relaunch.scenarios] host_reboot must be a boolean",
            id="wrong-type",
        ),
        pytest.param(
            "[iterm2.persistence.restore.relaunch.scenarios]\nfresh_launch = true\n",
            "[iterm2.persistence.restore.relaunch.scenarios] has no key 'fresh_launch'",
            id="unknown-scenario",
        ),
    ],
)
def test_given_a_bad_relaunch_key_when_loaded_then_the_error_names_its_table_and_key(iterm2_toml, toml_text, message):
    iterm2_toml.write_text(toml_text, encoding="utf-8")

    with pytest.raises(PersistenceConfigError) as exc_info:
        load_persistence_config()

    assert message in str(exc_info.value)


def test_given_file_edited_between_two_calls_when_loaded_again_then_new_value_is_returned(iterm2_toml):
    iterm2_toml.write_text("[iterm2.persistence.tracking]\nenabled = true\n", encoding="utf-8")
    assert load_persistence_config()["tracking"]["enabled"] is True

    iterm2_toml.write_text("[iterm2.persistence.tracking]\nenabled = false\n", encoding="utf-8")

    assert load_persistence_config()["tracking"]["enabled"] is False


@pytest.mark.parametrize(
    ("toml_text", "key", "expected"),
    [
        ('[iterm2.persistence.tracking]\ninclude = "c-*"\n', "include", "a list of strings"),
        ('[iterm2.persistence]\nenabled = "yes"\n', "enabled", "a boolean"),
        ("[iterm2.persistence.restore]\nmax_sessions = true\n", "max_sessions", "a non-negative integer"),
        ('[iterm2.persistence.restore]\nstagger_seconds = "fast"\n', "stagger_seconds", "a non-negative number"),
        ("[iterm2.persistence.restore]\ndefault_arrangement = 3\n", "default_arrangement", "a string"),
        ('[iterm2.persistence.restore]\nmode = "everything"\n', "mode", "one of"),
    ],
)
def test_given_key_of_wrong_type_when_loaded_then_one_error_names_key_and_expected_type(
    iterm2_toml, toml_text, key, expected
):
    iterm2_toml.write_text(toml_text, encoding="utf-8")

    with pytest.raises(PersistenceConfigError) as exc_info:
        load_persistence_config()

    message = str(exc_info.value)
    assert key in message
    assert expected in message


def test_given_unknown_key_when_loaded_then_error_names_it_instead_of_ignoring_it(iterm2_toml):
    """A misspelt switch must not leave the feature at a default the user meant to change."""
    iterm2_toml.write_text("[iterm2.persistence.tracking]\nenable = false\n", encoding="utf-8")

    with pytest.raises(PersistenceConfigError, match="'enable'"):
        load_persistence_config()
