"""Session names are ``{repo-alias}-{host-alias}-{optional-slug}-{N}``.

Driven through ``ai c --dry-run``, the entry point that resolves a name and prints
it without creating anything, so each test reads the name a real launch would use.
"""

from unittest.mock import patch

import pytest
from conftest import TEST_HOST_ALIAS, run_cli

import ai_cli.session_naming as session_naming

# Captured before the suite-wide fixture swaps them for a temp file, so the tests of
# resolution itself can put the real lookup back.
_REAL_HOST_ID = session_naming.host_id
_REAL_ALIAS_SOURCES = session_naming.alias_sources


def _dry_run(*args: str, tmux_sessions: list[str] | None = None, repo="/tmp/repo", env=None):
    with (
        patch("ai_cli.session.detect_repo_root", return_value=repo),
        patch("ai_cli.session._tmux_session_names", return_value=list(tmux_sessions or [])),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
    ):
        return run_cli(["ai", "c", "--project-prefix", "myrepo", "--dry-run", *args], env=env)


def _planned_session(out: str) -> str:
    for line in out.splitlines():
        words = line.split()
        if "session" in words:
            return words[words.index("session") + 1]
    raise AssertionError(f"no session line in the plan:\n{out}")


def test_given_no_slug_when_launching_then_the_name_is_repo_host_index_without_a_double_hyphen():
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == f"myrepo-{TEST_HOST_ALIAS}-1"
    assert "--" not in _planned_session(out)


def test_given_a_slug_when_launching_then_it_sits_between_the_host_alias_and_the_index():
    code, out, err = _dry_run("review")
    assert code == 0, err
    assert _planned_session(out) == f"myrepo-{TEST_HOST_ALIAS}-review-1"


def test_given_slot_one_taken_when_launching_then_the_index_auto_increments():
    code, out, err = _dry_run(tmux_sessions=[f"c-myrepo-{TEST_HOST_ALIAS}-1"])
    assert code == 0, err
    assert _planned_session(out) == f"myrepo-{TEST_HOST_ALIAS}-2"


def test_given_a_new_form_name_when_launching_then_it_maps_back_to_its_own_slot():
    taken = f"c-myrepo-{TEST_HOST_ALIAS}-review-3"
    code, out, err = _dry_run(f"myrepo-{TEST_HOST_ALIAS}-review-3", tmux_sessions=[taken])
    assert code == 0, err
    assert _planned_session(out) == f"myrepo-{TEST_HOST_ALIAS}-review-3"
    assert f"tmux target: {taken}" in out


def test_given_a_legacy_named_live_session_when_its_slot_is_named_then_the_old_form_still_resolves():
    code, out, err = _dry_run("3", tmux_sessions=["c-myrepo-3"])
    assert code == 0, err
    assert _planned_session(out) == "myrepo-3"
    assert "tmux target: c-myrepo-3" in out


def test_given_a_legacy_name_with_a_slug_when_named_in_full_then_the_old_form_still_resolves():
    code, out, err = _dry_run("myrepo-review-2", tmux_sessions=["c-myrepo-review-2"])
    assert code == 0, err
    assert _planned_session(out) == "myrepo-review-2"


def test_given_no_legacy_session_when_a_slot_is_named_then_it_is_created_in_the_new_form():
    code, out, err = _dry_run("4", tmux_sessions=["c-myrepo-3"])
    assert code == 0, err
    assert _planned_session(out) == f"myrepo-{TEST_HOST_ALIAS}-4"


# --- host alias resolution --------------------------------------------------


@pytest.fixture
def real_alias_lookup(monkeypatch, tmp_path):
    """The real lookup, pointed at a temp projects dir and a temp config home."""
    monkeypatch.setattr(session_naming, "host_id", _REAL_HOST_ID)
    monkeypatch.setattr(session_naming, "alias_sources", _REAL_ALIAS_SOURCES)
    projects = tmp_path / "projects"
    harness_config = projects / "ai-harness" / "config"
    harness_config.mkdir(parents=True)
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("AI_HOST", "test-box")
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr("ai_cli.config.get_xdg_config_home", lambda: config_home)
    return {
        "harness": harness_config / session_naming.ALIAS_FILENAME,
        "local": config_home / session_naming.ALIAS_FILENAME,
        "projects": projects,
    }


def _write_aliases(path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_given_both_sources_name_the_host_when_launching_then_the_ai_harness_registry_wins(real_alias_lookup):
    _write_aliases(real_alias_lookup["harness"], 'max_length = 5\n[aliases]\ntest-box = "abc"\n')
    _write_aliases(real_alias_lookup["local"], '[aliases]\ntest-box = "xyz"\n')
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-abc-1"


def test_given_the_registry_lacks_the_host_when_launching_then_the_local_file_answers(real_alias_lookup):
    _write_aliases(real_alias_lookup["harness"], '[aliases]\nother-box = "oth"\n')
    _write_aliases(real_alias_lookup["local"], '[aliases]\ntest-box = "xyz"\n')
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-xyz-1"


def test_given_no_ai_harness_checkout_when_launching_then_the_local_file_answers(real_alias_lookup, tmp_path):
    _write_aliases(real_alias_lookup["local"], '[aliases]\ntest-box = "xyz"\n')
    code, out, err = _dry_run(env={"PROJECTS_DIR": str(tmp_path / "elsewhere")})
    assert code == 0, err
    assert _planned_session(out) == "myrepo-xyz-1"


def test_given_an_unconfigured_host_when_launching_then_it_refuses_naming_the_config_file(real_alias_lookup):
    _write_aliases(real_alias_lookup["harness"], '[aliases]\nother-box = "oth"\n')
    code, out, err = _dry_run()
    assert code == 1
    assert "dry run" not in out
    assert "no host alias is configured" in err
    assert "test-box" in err
    assert str(real_alias_lookup["harness"]) in err
    assert str(real_alias_lookup["local"]) in err


def test_given_an_alias_longer_than_max_length_when_launching_then_it_refuses(real_alias_lookup):
    _write_aliases(real_alias_lookup["harness"], 'max_length = 3\n[aliases]\ntest-box = "abcd"\n')
    code, _out, err = _dry_run()
    assert code == 1
    assert "invalid" in err and str(real_alias_lookup["harness"]) in err


def test_given_an_alias_with_a_digit_when_launching_then_it_refuses(real_alias_lookup):
    _write_aliases(real_alias_lookup["harness"], '[aliases]\ntest-box = "ab1"\n')
    code, _out, err = _dry_run()
    assert code == 1
    assert "lowercase letters only" in err
