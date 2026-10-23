"""Where the naming settings come from, and the config template (T-3.4).

``enabled`` and ``name_with_label`` each resolve on their own, first hit wins: flag,
environment, local config, the shared policy file's host table, its defaults, built in.
The two length limits come from the published policy wherever a shared registry exists.
"""

from __future__ import annotations

import tomllib

import pytest
from click.testing import CliRunner
from name_fleet import HOST, build_fleet

from ai_cli import config as _config
from ai_cli import name_registry as reg
from ai_cli import session_title
from ai_cli.main import _cli_group

LAYERS = ["flag", "environment", "local", "host", "defaults", "built-in"]
LAYER_LABEL = {
    "flag": "flag",
    "environment": "environment",
    "local": "[session_names]",
    "host": f"[hosts.{HOST}]",
    "defaults": "[defaults]",
}
VALUES = {"enabled": (True, False), "name_with_label": ("drop", "keep")}
ENV = {"enabled": session_title.ENV_ENABLED, "name_with_label": session_title.ENV_WITH_LABEL}


def _env_value(key: str, value) -> str:
    return ("1" if value else "0") if key == "enabled" else str(value)


def _write_local(key: str | None, value=None) -> None:
    path = _config.get_xdg_config_home() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ""
    if key is not None:
        rendered = ("true" if value else "false") if isinstance(value, bool) else f'"{value}"'
        body = f"[session_names]\n{key} = {rendered}\n"
    path.write_text(body)


def _resolve(key: str, layers: dict[str, object], monkeypatch) -> session_title.Setting:
    """Resolve ``key`` with only the given layers set (each to its own value)."""
    monkeypatch.delenv(session_title.ENV_ENABLED, raising=False)
    monkeypatch.delenv(session_title.ENV_WITH_LABEL, raising=False)
    if "environment" in layers:
        monkeypatch.setenv(ENV[key], _env_value(key, layers["environment"]))
    _write_local(key if "local" in layers else None, layers.get("local"))
    policy: dict = {}
    if "host" in layers:
        policy["hosts"] = {HOST: {key: layers["host"]}}
    if "defaults" in layers:
        policy["defaults"] = {key: layers["defaults"]}
    flag = layers.get("flag")
    return session_title.resolve_settings(
        enabled_flag=flag if key == "enabled" else None,
        with_label_flag=flag if key == "name_with_label" else None,
        policy_config=policy,
        host=HOST,
    )[key]


@pytest.mark.parametrize("key", ["enabled", "name_with_label"])
@pytest.mark.parametrize("higher", range(len(LAYERS) - 1))
def test_given_a_key_set_in_two_adjacent_layers_when_resolved_then_the_higher_layer_wins(monkeypatch, key, higher):
    upper, lower = LAYERS[higher], LAYERS[higher + 1]
    wins, loses = VALUES[key]
    layers = {upper: wins}
    if lower != "built-in":
        layers[lower] = loses

    setting = _resolve(key, layers, monkeypatch)

    assert setting.value == wins
    assert LAYER_LABEL[upper] in setting.layer


@pytest.mark.parametrize("key", ["enabled", "name_with_label"])
def test_given_no_layer_set_when_resolved_then_the_built_in_value_applies(monkeypatch, key):
    setting = _resolve(key, {}, monkeypatch)

    assert setting == session_title.Setting(session_title.BUILT_IN[key], "built-in")


@pytest.mark.parametrize(("raw", "expected"), [("1", True), ("0", False)])
def test_given_the_enabled_environment_variable_when_resolved_then_it_sets_enabled(monkeypatch, raw, expected):
    monkeypatch.setenv(session_title.ENV_ENABLED, raw)
    _write_local(None)

    settings = session_title.resolve_settings(enabled_flag=None, with_label_flag=None, policy_config={}, host=HOST)

    assert settings["enabled"].value is expected


@pytest.mark.parametrize("raw", ["keep", "drop"])
def test_given_the_label_environment_variable_when_resolved_then_it_sets_name_with_label(monkeypatch, raw):
    monkeypatch.setenv(session_title.ENV_WITH_LABEL, raw)
    _write_local(None)

    settings = session_title.resolve_settings(enabled_flag=None, with_label_flag=None, policy_config={}, host=HOST)

    assert settings["name_with_label"].value == raw


def test_given_one_flag_when_resolved_then_it_overrides_only_its_own_key(monkeypatch):
    monkeypatch.setenv(session_title.ENV_ENABLED, "0")
    monkeypatch.setenv(session_title.ENV_WITH_LABEL, "drop")

    settings = session_title.resolve_settings(enabled_flag=True, with_label_flag=None, policy_config={}, host=HOST)

    assert settings["enabled"] == session_title.Setting(True, "flag")
    assert settings["name_with_label"].value == "drop"
    assert "environment" in settings["name_with_label"].layer


@pytest.mark.parametrize(
    ("variable", "value", "named"),
    [
        (session_title.ENV_ENABLED, "yes", "1 (human names) or 0"),
        (session_title.ENV_WITH_LABEL, "both", "keep or drop"),
    ],
)
def test_given_an_invalid_environment_value_when_launching_then_it_exits_two_before_reserving(
    tmp_path, monkeypatch, variable, value, named
):
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    before = fleet.tip()
    from name_fleet import quiet_reporter

    from ai_cli import main

    reporter, stream = quiet_reporter()
    with pytest.raises(SystemExit) as caught:
        main._resolve_session_identity(
            "c",
            "myrepo-box",
            "",
            {},
            is_remote=False,
            use_tmux=True,
            legacy_prefix="myrepo",
            naming=main._NamingRequest(),
            repo="myrepo",
            repo_alias="myrepo",
            host_alias="box",
            worktree=True,
            dry_run=False,
            reporter=reporter,
        )

    assert caught.value.code == 2
    assert variable in stream.getvalue() and named in stream.getvalue()
    assert fleet.tip() == before


def test_given_a_shared_registry_when_limits_resolve_then_the_published_policy_decides_and_local_values_warn(
    tmp_path, monkeypatch
):
    fleet = build_fleet(tmp_path, monkeypatch)
    path = _config.get_xdg_config_home() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[session_names]\nmax_name_length = 9\n")

    limits = session_title.resolve_limits(fleet.registry, fleet.registry.snapshot())
    result = CliRunner().invoke(_cli_group, ["show-name-config"])

    assert (limits.low, limits.high) == (3, 4)
    assert "published policy" in limits.layer
    assert limits.warning is not None and "session-names.toml [limits]" in limits.warning
    assert "has no effect" in result.output


def test_given_no_shared_registry_when_limits_resolve_then_local_config_then_built_in_decides(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(_config, "_get_projects_dir", lambda: projects)
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    registry = reg.discover().registry
    _write_local(None)

    built_in = session_title.resolve_limits(registry)
    (_config.get_xdg_config_home() / "config.toml").write_text(
        "[session_names]\nmin_name_length = 2\nmax_name_length = 6\n"
    )
    local = session_title.resolve_limits(registry)

    assert (built_in.low, built_in.high, built_in.layer) == (3, 4, "built-in")
    assert (local.low, local.high) == (2, 6)
    assert "[session_names]" in local.layer
    assert local.warning is None


def test_given_a_per_launch_length_option_when_launched_then_it_is_handled_as_any_unknown_option():
    """The earlier draft's per-launch length override is gone (parity break, recorded)."""
    from ai_cli import main

    seen: list[dict] = []

    def capture(ctx, **options):
        seen.append({"options": options, "args": list(ctx.args)})

    command = main.click.command(context_settings=main.SESSION_CONTEXT)(main._session_options(capture))
    CliRunner().invoke(command, ["probe", "--name-max-length", "6"])
    CliRunner().invoke(command, ["probe", "--some-engine-flag", "6"])

    length, unknown = seen
    assert length["args"] == ["--name-max-length", "6"]
    assert unknown["args"] == ["--some-engine-flag", "6"]
    assert length["options"] == unknown["options"]


def test_given_the_old_length_environment_variable_when_limits_resolve_then_it_has_no_effect(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.setenv("AI_CLI_SESSION_NAME_MAX_LENGTH", "6")

    limits = session_title.resolve_limits(fleet.registry, fleet.registry.snapshot())

    assert limits.high == 4


def test_given_a_fresh_config_when_written_from_the_template_then_it_carries_the_session_names_table():
    parsed = tomllib.loads(_config.DEFAULT_CONFIG)

    assert parsed["session_names"] == {
        "enabled": True,
        "name_with_label": "keep",
        "min_name_length": 3,
        "max_name_length": 4,
    }
    section = _config.DEFAULT_CONFIG[_config.DEFAULT_CONFIG.index("[session_names]") :]
    assert section.count("Used only where no shared name registry exists") == 1
    assert "c-{project}-{n}" not in _config.DEFAULT_CONFIG


def test_given_a_shared_registry_when_the_config_is_shown_then_every_key_has_its_value_layer_and_the_policy_state(
    tmp_path, monkeypatch
):
    build_fleet(tmp_path, monkeypatch)
    monkeypatch.setenv(session_title.ENV_ENABLED, "1")

    result = CliRunner().invoke(_cli_group, ["show-name-config"])

    assert result.exit_code == 0, result.output
    for key in ("enabled", "name_with_label", "min_name_length", "max_name_length", "pick_floor", "offline_spares"):
        assert key in result.output
    assert "environment AI_CLI_SESSION_NAMES" in result.output
    assert "published policy   commit" in result.output
    assert "this checkout      commit" in result.output
    assert "ancestry state     same" in result.output
