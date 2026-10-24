"""The ``[naming]`` section chooses which session-name conventions ``ai`` accepts.

During a cutover both conventions work: the alias form ``{repo-alias}-{host-alias}-{N}``
and the old ``{repo-alias}-{N}`` form. Each is a config switch read on every launch, so
retiring the old form is a config change only. Driven through ``ai c --dry-run`` and the
remote allocation reply, the entry points that resolve a name without creating anything.
"""

import json
from unittest.mock import patch

import pytest
from conftest import run_cli
from test_session_naming import (
    _planned_session,
    _write_aliases,
    real_alias_lookup,  # noqa: F401  (registers the fixture; used via `registry`)
)


@pytest.fixture
def registry(request):
    """The real host-alias lookup over a temp tree (the fixture imported above)."""
    return request.getfixturevalue("real_alias_lookup")


CONFIGURED = '[aliases]\ntest-box = "xyz"\n'
UNRELATED = '[aliases]\nother-box = "oth"\n'


def _naming(**keys) -> dict:
    return {"naming": keys}


def _launch(config: dict, *args: str, tmux_sessions: list[str] | None = None):
    with (
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.session._tmux_session_names", return_value=list(tmux_sessions or [])),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
    ):
        return run_cli(["ai", "c", "--project-prefix", "myrepo", "--dry-run", *args], config=config)


def test_given_no_naming_section_and_a_configured_host_when_launching_then_the_alias_form_is_used(registry):
    _write_aliases(registry["environment"], CONFIGURED)
    code, out, err = _launch({})
    assert code == 0, err
    assert _planned_session(out) == "myrepo-xyz-1"


def test_given_no_host_alias_and_the_default_cutover_config_when_launching_then_the_old_form_is_used(registry):
    _write_aliases(registry["environment"], UNRELATED)
    code, out, err = _launch({})
    assert code == 0, err
    assert _planned_session(out) == "myrepo-1"


def test_given_no_host_alias_and_the_old_form_off_when_launching_then_it_refuses_naming_the_keys(registry):
    _write_aliases(registry["environment"], UNRELATED)
    code, _out, err = _launch(_naming(legacy_convention="off"))
    assert code == 1
    assert "no host alias is configured" in err
    assert "naming.legacy_convention" in err


def test_given_the_alias_form_off_when_launching_then_the_old_form_names_the_session(registry):
    _write_aliases(registry["environment"], CONFIGURED)
    code, out, err = _launch(_naming(alias_convention=False))
    assert code == 0, err
    assert _planned_session(out) == "myrepo-1"


def test_given_both_forms_off_when_launching_then_it_refuses_naming_both_keys(registry):
    _write_aliases(registry["environment"], CONFIGURED)
    code, out, err = _launch(_naming(alias_convention=False, legacy_convention="off"))
    assert code == 1
    assert "dry run" not in out
    assert "naming.alias_convention" in err and "naming.legacy_convention" in err


def test_given_a_live_old_named_slot_and_the_default_config_when_launching_its_index_then_it_is_reached(
    registry,
):
    _write_aliases(registry["environment"], CONFIGURED)
    code, out, err = _launch({}, "3", tmux_sessions=["c-myrepo-3"])
    assert code == 0, err
    assert _planned_session(out) == "myrepo-3"


def test_given_a_live_old_named_slot_and_the_old_form_off_when_launching_its_index_then_the_alias_slot_is_planned(
    registry,
):
    _write_aliases(registry["environment"], CONFIGURED)
    code, out, err = _launch(_naming(legacy_convention="off"), "3", tmux_sessions=["c-myrepo-3"])
    assert code == 0, err
    assert _planned_session(out) == "myrepo-xyz-3"


def test_given_the_old_form_reach_only_and_the_alias_form_off_when_launching_then_it_refuses(registry):
    _write_aliases(registry["environment"], CONFIGURED)
    code, _out, err = _launch(_naming(alias_convention=False, legacy_convention="reach"))
    assert code == 1
    assert "naming.legacy_convention" in err and "full" in err


@pytest.mark.parametrize(
    ("keys", "key"),
    [
        ({"legacy_convention": "sometimes"}, "naming.legacy_convention"),
        ({"alias_convention": "yes"}, "naming.alias_convention"),
    ],
)
def test_given_an_invalid_naming_value_when_launching_then_it_refuses_naming_the_key(registry, keys, key):
    _write_aliases(registry["environment"], CONFIGURED)
    code, _out, err = _launch(_naming(**keys))
    assert code == 1
    assert key in err


def test_given_no_host_alias_when_a_remote_launch_allocates_then_the_reply_carries_the_old_form(registry):
    """The Mac's ``ai c N -R`` asks the remote host to allocate; an unmapped host id must not end it."""
    _write_aliases(registry["environment"], UNRELATED)
    with (
        patch("ai_cli.host_env.resolve_host_env", return_value=("inherited", "test-box")),
        patch("ai_cli.session._tmux_session_names", return_value=[]),
    ):
        code, out, err = run_cli(["ai", "internal", "allocate-session-name", "c", "myrepo", "4", "--project", "myrepo"])
    assert code == 0, err
    assert json.loads(out)["ai_name"] == "myrepo-4"
