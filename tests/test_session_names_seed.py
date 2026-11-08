"""The config template seeds no active ``[session_names]`` key, and an old seed is cleaned.

A seeded key sits above the shared policy file's ``[hosts.<host id>]`` and ``[defaults]``
layers, so an active one in the template would hide them on every machine. An older
template did seed four of them; the first ``ai`` command after an upgrade comments out
each of those whose value is still the seeded one and leaves every other key alone.
"""

from __future__ import annotations

import tomllib

import pytest
from click.testing import CliRunner
from name_fleet import HOST, HOST_ALIAS, POLICY_TOML, REPO, STEM, build_fleet, quiet_reporter

from ai_cli import main, session_title
from ai_cli.main import _cli_group

POLICY_WITH_HOST = POLICY_TOML + f"\n[hosts.{HOST}]\nenabled = false\n"

OLD_SEEDED_TABLE = """[session_names]
## Name each new session with a short human first name in place of an index:
## {repo-alias}-{host-alias}-[{label}-]{name}, for example myproject-box-ada.
## A name is unique on every machine that shares a name registry. Set false for
## index names ({repo-alias}-{host-alias}-[{label}-]{N}). -S/--session-names and
## -I/--no-session-names override this for one launch.
enabled = true
## With a label (`ai c review`): "keep" names the session myproject-box-review-ada,
## "drop" names it myproject-box-review. -w/--with-name and -u/--no-name override it.
name_with_label = "keep"
## Name length limits. Used only where no shared name registry exists; a shared
## registry's limits apply to every machine that uses it, and these are then ignored.
min_name_length = 3
max_name_length = 4
"""

OTHER_CONTENT_BEFORE = """## my own note about this machine
[machine]
host_id = "box-one"
os_type = "linux"

"""

OTHER_CONTENT_AFTER = """
[session_relaunch]
on_boot = false
"""


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    home = tmp_path / "config-home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home))
    monkeypatch.delenv(session_title.ENV_ENABLED, raising=False)
    monkeypatch.delenv(session_title.ENV_WITH_LABEL, raising=False)
    return home / "ai-cli-utils" / "config.toml"


def _setting_line(output: str, key: str) -> str:
    return next(line for line in output.splitlines() if line.removeprefix("[names] config: ").startswith(f"{key} "))


def _session_names(path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("session_names", {})


def test_given_an_empty_config_home_when_show_name_config_runs_then_no_key_is_seeded_and_the_shared_layer_applies(
    tmp_path, monkeypatch, config_home
):
    build_fleet(tmp_path, monkeypatch, policy_toml=POLICY_WITH_HOST)

    result = CliRunner().invoke(_cli_group, ["show-name-config"])

    assert result.exit_code == 0, result.output
    assert config_home.is_file()
    assert _session_names(config_home) == {}
    assert "False" in _setting_line(result.output, "enabled")
    assert f"[hosts.{HOST}]" in _setting_line(result.output, "enabled")
    assert "[defaults]" in _setting_line(result.output, "name_with_label")
    assert "has no effect" not in result.output


def test_given_a_freshly_seeded_config_when_a_launch_names_its_session_then_no_length_warning_is_printed(
    tmp_path, monkeypatch, config_home
):
    build_fleet(tmp_path, monkeypatch)
    monkeypatch.setenv(session_title.ENV_ENABLED, "1")
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    reporter, stream = quiet_reporter()

    _session_id, title = main._resolve_session_identity(
        "c",
        STEM,
        "",
        {},
        is_remote=False,
        use_tmux=True,
        legacy_prefix=REPO,
        naming=main._NamingRequest(),
        repo=REPO,
        repo_alias=REPO,
        host_alias=HOST_ALIAS,
        worktree=True,
        dry_run=True,
        reporter=reporter,
    )

    assert config_home.is_file()
    assert title.startswith(f"{STEM}-")
    assert "has no effect" not in stream.getvalue()
    assert "min_name_length" not in stream.getvalue()


def test_given_a_config_seeded_by_the_old_template_when_ai_runs_then_the_seeded_keys_are_dropped_and_the_rest_kept(
    tmp_path, monkeypatch, config_home
):
    build_fleet(tmp_path, monkeypatch, policy_toml=POLICY_WITH_HOST)
    config_home.parent.mkdir(parents=True)
    config_home.write_text(OTHER_CONTENT_BEFORE + OLD_SEEDED_TABLE + OTHER_CONTENT_AFTER, encoding="utf-8")

    result = CliRunner().invoke(_cli_group, ["show-name-config"])

    assert result.exit_code == 0, result.output
    text = config_home.read_text(encoding="utf-8")
    assert _session_names(config_home) == {}
    assert text.startswith(OTHER_CONTENT_BEFORE)
    assert text.endswith(OTHER_CONTENT_AFTER)
    assert "## Name each new session with a short human first name in place of an index:" in text
    assert f"[hosts.{HOST}]" in _setting_line(result.output, "enabled")
    assert "has no effect" not in result.output


def test_given_an_old_seed_with_a_changed_value_when_ai_runs_then_the_changed_value_is_kept(
    tmp_path, monkeypatch, config_home
):
    build_fleet(tmp_path, monkeypatch, policy_toml=POLICY_WITH_HOST)
    config_home.parent.mkdir(parents=True)
    changed = OLD_SEEDED_TABLE.replace('name_with_label = "keep"', 'name_with_label = "drop"').replace(
        "max_name_length = 4", "max_name_length = 6"
    )
    config_home.write_text(OTHER_CONTENT_BEFORE + changed, encoding="utf-8")

    result = CliRunner().invoke(_cli_group, ["show-name-config"])

    assert result.exit_code == 0, result.output
    assert _session_names(config_home) == {"name_with_label": "drop", "max_name_length": 6}
    assert f"[hosts.{HOST}]" in _setting_line(result.output, "enabled")
    assert "[session_names]" in _setting_line(result.output, "name_with_label")


def test_given_a_cleaned_old_seed_when_the_user_sets_a_key_to_the_old_value_then_later_runs_keep_it(
    tmp_path, monkeypatch, config_home
):
    build_fleet(tmp_path, monkeypatch, policy_toml=POLICY_WITH_HOST)
    config_home.parent.mkdir(parents=True)
    config_home.write_text(OTHER_CONTENT_BEFORE + OLD_SEEDED_TABLE, encoding="utf-8")
    CliRunner().invoke(_cli_group, ["show-name-config"])
    config_home.write_text(config_home.read_text(encoding="utf-8") + "enabled = true\n", encoding="utf-8")

    result = CliRunner().invoke(_cli_group, ["show-name-config"])

    assert _session_names(config_home) == {"enabled": True}
    assert "[session_names]" in _setting_line(result.output, "enabled")


def test_given_a_hand_written_table_with_the_old_values_when_ai_runs_then_it_is_left_alone(
    tmp_path, monkeypatch, config_home
):
    build_fleet(tmp_path, monkeypatch, policy_toml=POLICY_WITH_HOST)
    config_home.parent.mkdir(parents=True)
    body = OTHER_CONTENT_BEFORE + '[session_names]\nenabled = true\nname_with_label = "keep"\n'
    config_home.write_text(body, encoding="utf-8")

    CliRunner().invoke(_cli_group, ["show-name-config"])

    assert config_home.read_text(encoding="utf-8") == body
