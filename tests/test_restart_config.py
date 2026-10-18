"""Restart settings are validated without coercing unsafe input."""

import tomllib

import pytest

from ai_cli.config import DEFAULT_CONFIG, RESTART_SESSION_DEFAULTS, read_restart_config


def test_given_default_config_when_read_then_restart_is_enabled_with_safe_defaults():
    assert read_restart_config(tomllib.loads(DEFAULT_CONFIG)) == RESTART_SESSION_DEFAULTS
    assert read_restart_config({})["context_threshold_pct"] == 20
    assert read_restart_config({})["auto"] is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("context_threshold_pct", 101),
        ("context_threshold_pct", True),
        ("settle_seconds", -1),
        ("term_grace_seconds", float("nan")),
        ("cooldown_seconds", "0"),
        ("pre_step", 1),
        ("auto", "true"),
        ("mechanism", "group"),
        ("compaction_fresh_turns", 1.5),
    ],
)
def test_given_invalid_setting_when_read_then_restart_refuses(key, value):
    with pytest.raises(ValueError, match=key):
        read_restart_config({"restart_session": {key: value}})


def test_given_changed_config_when_read_again_then_new_threshold_is_used():
    config = {"restart_session": {"context_threshold_pct": 10}}
    assert read_restart_config(config)["context_threshold_pct"] == 10
    config["restart_session"]["context_threshold_pct"] = 30
    assert read_restart_config(config)["context_threshold_pct"] == 30
