"""Session names are ``{repo-alias}-{host-alias}-{optional-slug}-{N}``.

Driven through ``ai c --dry-run``, the entry point that resolves a name and prints
it without creating anything, so each test reads the name a real launch would use.
"""

import io
import json
import os
import sys
import tomllib
from unittest.mock import patch

import pytest
from conftest import TEST_HOST_ALIAS, run_cli

import ai_cli.session_naming as session_naming

# Captured before the suite-wide fixture swaps them for a temp file, so the tests of
# resolution itself can put the real lookup back.
_REAL_HOST_ID = session_naming.host_id
_REAL_ALIAS_SOURCES = session_naming.alias_sources


# The cutover default falls back to the old name form when a host has no alias; the tests of the
# refusal itself turn that fallback off.
STRICT_ALIAS_FORM = {"naming": {"legacy_convention": "off"}}


def _dry_run(*args: str, tmux_sessions: list[str] | None = None, repo="/tmp/repo", env=None, config=None):
    with (
        patch("ai_cli.session.detect_repo_root", return_value=repo),
        patch("ai_cli.session._tmux_session_names", return_value=list(tmux_sessions or [])),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
    ):
        return run_cli(["ai", "c", "--project-prefix", "myrepo", "--dry-run", *args], config=config, env=env)


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
    environment_config = projects / "myhub" / "config"
    environment_config.mkdir(parents=True)
    monkeypatch.setattr("ai_cli.config.get_environment_repo", lambda: "myhub")
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("AI_HOST", "test-box")
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr("ai_cli.config.get_xdg_config_home", lambda: config_home)
    # The suite points projects_dir at this checkout's parent, whose siblings would
    # otherwise join sibling discovery; resolution tests see only the temp tree.
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects)
    return {
        "environment": environment_config / session_naming.ALIAS_FILENAME,
        "local": config_home / session_naming.ALIAS_FILENAME,
        "projects": projects,
    }


def _sibling(projects, repo: str):
    return projects / repo / "config" / session_naming.ALIAS_FILENAME


def _write_aliases(path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_given_both_sources_name_the_host_when_launching_then_the_environment_repo_registry_wins(real_alias_lookup):
    _write_aliases(real_alias_lookup["environment"], 'max_length = 5\n[aliases]\ntest-box = "abc"\n')
    _write_aliases(real_alias_lookup["local"], '[aliases]\ntest-box = "xyz"\n')
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-abc-1"


def test_given_the_registry_lacks_the_host_when_launching_then_the_local_file_answers(real_alias_lookup):
    _write_aliases(real_alias_lookup["environment"], '[aliases]\nother-box = "oth"\n')
    _write_aliases(real_alias_lookup["local"], '[aliases]\ntest-box = "xyz"\n')
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-xyz-1"


def test_given_no_environment_repo_checkout_when_launching_then_the_local_file_answers(real_alias_lookup, tmp_path):
    _write_aliases(real_alias_lookup["local"], '[aliases]\ntest-box = "xyz"\n')
    code, out, err = _dry_run(env={"PROJECTS_DIR": str(tmp_path / "elsewhere")})
    assert code == 0, err
    assert _planned_session(out) == "myrepo-xyz-1"


def test_given_an_unconfigured_host_when_launching_then_it_refuses_naming_the_config_file(real_alias_lookup):
    _write_aliases(real_alias_lookup["environment"], '[aliases]\nother-box = "oth"\n')
    code, out, err = _dry_run(config=STRICT_ALIAS_FORM)
    assert code == 1
    assert "dry run" not in out
    assert "no host alias is configured" in err
    assert "test-box" in err
    assert str(real_alias_lookup["environment"]) in err
    assert str(real_alias_lookup["local"]) in err


def test_given_an_alias_longer_than_max_length_when_launching_then_it_refuses(real_alias_lookup):
    _write_aliases(real_alias_lookup["environment"], 'max_length = 3\n[aliases]\ntest-box = "abcd"\n')
    code, _out, err = _dry_run()
    assert code == 1
    assert "invalid" in err and str(real_alias_lookup["environment"]) in err


def test_given_an_alias_with_a_digit_after_a_letter_when_launching_then_it_names_the_session(real_alias_lookup):
    # A letters-only rule refused a real alias like `ec2`, and every `ai c` on that host exited.
    _write_aliases(real_alias_lookup["environment"], 'max_length = 5\n[aliases]\ntest-box = "ec2"\n')
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-ec2-1"


def test_given_an_alias_starting_with_a_digit_when_launching_then_it_refuses(real_alias_lookup):
    _write_aliases(real_alias_lookup["environment"], '[aliases]\ntest-box = "2ec"\n')
    code, _out, err = _dry_run()
    assert code == 1
    assert "starting with a letter" in err


# --- sibling registries, discovered by shape ----------------------------------


def test_given_the_environment_registry_names_the_host_when_launching_then_no_sibling_file_is_read(real_alias_lookup):
    _write_aliases(real_alias_lookup["environment"], '[aliases]\ntest-box = "abc"\n')
    # Unparseable, so reading it at all would turn the launch into a refusal.
    _write_aliases(_sibling(real_alias_lookup["projects"], "config-repo"), "not toml {{{\n")
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-abc-1"


def test_given_no_environment_repo_configured_when_its_registry_disagrees_then_it_is_only_a_sibling(
    real_alias_lookup, monkeypatch
):
    """Without ``[project] environment_repo`` no sibling outranks another, so a conflict refuses."""
    monkeypatch.setattr("ai_cli.config.get_environment_repo", lambda: "")
    _write_aliases(real_alias_lookup["environment"], '[aliases]\ntest-box = "abc"\n')
    _write_aliases(_sibling(real_alias_lookup["projects"], "config-repo"), '[aliases]\ntest-box = "sib"\n')
    code, _out, err = _dry_run()
    assert code == 1
    assert "disagree" in err


def test_given_only_a_sibling_registry_names_the_host_when_launching_then_its_alias_is_used(real_alias_lookup):
    _write_aliases(real_alias_lookup["environment"], '[aliases]\nother-box = "oth"\n')
    _write_aliases(_sibling(real_alias_lookup["projects"], "config-repo"), '[aliases]\ntest-box = "sib"\n')
    _write_aliases(real_alias_lookup["local"], '[aliases]\ntest-box = "xyz"\n')
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-sib-1"


def test_given_two_siblings_disagree_about_the_host_when_launching_then_it_refuses_naming_both(real_alias_lookup):
    first = _sibling(real_alias_lookup["projects"], "alpha")
    second = _sibling(real_alias_lookup["projects"], "beta")
    _write_aliases(first, '[aliases]\ntest-box = "one"\n')
    _write_aliases(second, '[aliases]\ntest-box = "two"\n')
    code, out, err = _dry_run()
    assert code == 1
    assert "dry run" not in out
    assert "disagree" in err
    assert f'{first}: test-box = "one"' in err
    assert f'{second}: test-box = "two"' in err


def test_given_two_siblings_agree_about_the_host_when_launching_then_the_alias_is_used(real_alias_lookup):
    _write_aliases(_sibling(real_alias_lookup["projects"], "alpha"), '[aliases]\ntest-box = "one"\n')
    _write_aliases(_sibling(real_alias_lookup["projects"], "beta"), '[aliases]\ntest-box = "one"\n')
    code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-one-1"


def test_given_no_registry_names_the_host_when_launching_then_every_path_is_listed_in_checked_order(
    real_alias_lookup,
):
    projects = real_alias_lookup["projects"]
    _write_aliases(_sibling(projects, "beta"), '[aliases]\nother-box = "oth"\n')
    _write_aliases(_sibling(projects, "alpha"), '[aliases]\nother-box = "oth"\n')
    code, _out, err = _dry_run(config=STRICT_ALIAS_FORM)
    assert code == 1
    order = [
        real_alias_lookup["environment"],
        _sibling(projects, "alpha"),
        _sibling(projects, "beta"),
        real_alias_lookup["local"],
    ]
    positions = [err.index(f"    {path}\n") for path in order]
    assert positions == sorted(positions), err


@pytest.mark.skipif(sys.platform == "win32", reason="a directory symlink needs a privilege Windows runners lack")
def test_given_the_projects_dir_reached_through_a_symlink_when_refused_then_each_registry_is_listed_once(
    real_alias_lookup, tmp_path, monkeypatch
):
    link = tmp_path / "projects-link"
    link.symlink_to(real_alias_lookup["projects"], target_is_directory=True)
    _write_aliases(real_alias_lookup["environment"], '[aliases]\nother-box = "oth"\n')
    monkeypatch.setenv("PROJECTS_DIR", str(link))
    code, _out, err = _dry_run(env={"PROJECTS_DIR": str(link)}, config=STRICT_ALIAS_FORM)
    assert code == 1
    assert err.count(f"myhub{os.sep}config{os.sep}{session_naming.ALIAS_FILENAME}\n") == 1, err


# --- the terminal prompt --------------------------------------------------------


class _Terminal(io.StringIO):
    """Typed input on a stdin that reports itself as a terminal."""

    def isatty(self) -> bool:
        return True


class _Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


def _allocate(stdin: io.StringIO, config=None):
    """``ai internal allocate-session-name``: resolves the alias and names a session, no dry run."""
    with (
        patch("sys.stdin", stdin),
        patch("ai_cli.host_env.resolve_host_env", return_value=("inherited", os.environ.get("AI_HOST", ""))),
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
    ):
        return run_cli(["ai", "internal", "allocate-session-name", "c", "myrepo", ""], config=config)


_COMMENTED_REGISTRY = (
    "# Fleet host aliases.\n"
    "max_length = 5\n"
    "\n"
    "[aliases]\n"
    'other-box = "oth"\n'
    "# a trailing note inside the table\n"
    "\n"
    "[notes]\n"
    'owner = "ops"\n'
)


def test_given_an_unconfigured_host_and_a_terminal_when_an_alias_is_typed_then_it_is_written_and_used(
    real_alias_lookup,
):
    environment = real_alias_lookup["environment"]
    _write_aliases(environment, _COMMENTED_REGISTRY)
    code, out, err = _allocate(_Terminal("box\n"))
    assert code == 0, err
    assert json.loads(out)["ai_name"] == "myrepo-box-1"
    assert "prefer 3 or fewer, at most 5" in err
    assert f"the limit is max_length in {environment}" in err
    data = tomllib.loads(environment.read_text(encoding="utf-8"))
    assert data["aliases"] == {"other-box": "oth", "test-box": "box"}
    assert "test-box" not in data["notes"]
    lines = environment.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Fleet host aliases."
    assert lines[lines.index('other-box = "oth"') + 1] == 'test-box = "box"'


def test_given_the_written_registry_is_in_a_git_checkout_when_prompted_then_it_says_commit_and_push(
    real_alias_lookup,
):
    environment = real_alias_lookup["environment"]
    _write_aliases(environment, '[aliases]\nother-box = "oth"\n')
    marker = environment.parent.parent / ".git"
    marker.mkdir()
    (marker / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    code, _out, err = _allocate(_Terminal("box\n"))
    assert code == 0, err
    assert f'Wrote test-box = "box" to {environment}' in err
    assert "git-tracked: commit and push it" in err


def test_given_the_written_registry_is_not_in_a_git_checkout_when_prompted_then_it_does_not_claim_tracking(
    real_alias_lookup,
):
    environment = real_alias_lookup["environment"]
    _write_aliases(environment, '[aliases]\nother-box = "oth"\n')
    code, _out, err = _allocate(_Terminal("box\n"))
    assert code == 0, err
    assert "Wrote test-box" in err
    assert "git-tracked" not in err


def test_given_only_a_sibling_registry_exists_when_prompted_then_the_alias_is_written_there(real_alias_lookup):
    sibling = _sibling(real_alias_lookup["projects"], "config-repo")
    _write_aliases(sibling, '[aliases]\nother-box = "oth"\n')
    code, _out, err = _allocate(_Terminal("box\n"))
    assert code == 0, err
    assert tomllib.loads(sibling.read_text(encoding="utf-8"))["aliases"]["test-box"] == "box"
    assert not real_alias_lookup["environment"].exists()
    assert not real_alias_lookup["local"].exists()


def test_given_no_registry_file_exists_when_prompted_then_the_local_file_is_created(real_alias_lookup):
    code, _out, err = _allocate(_Terminal("box\n"))
    assert code == 0, err
    data = tomllib.loads(real_alias_lookup["local"].read_text(encoding="utf-8"))
    assert data == {"max_length": 5, "aliases": {"test-box": "box"}}


@pytest.mark.parametrize(
    ("typed", "why"),
    [
        ("Box", "lowercase letters and digits"),
        ("2ab", "starting with a letter"),
        ("abcdef", "the limit is 5"),
        ("", "nothing was entered"),
    ],
)
def test_given_an_invalid_alias_is_typed_when_prompted_then_it_refuses_and_writes_nothing(
    real_alias_lookup, typed, why
):
    environment = real_alias_lookup["environment"]
    _write_aliases(environment, _COMMENTED_REGISTRY)
    before = environment.read_bytes()
    code, _out, err = _allocate(_Terminal(f"{typed}\n"))
    assert code == 1
    assert why in err
    assert "Nothing was written" in err
    assert environment.read_bytes() == before


def test_given_an_edit_that_would_not_parse_when_prompted_then_the_file_is_left_byte_identical(real_alias_lookup):
    environment = real_alias_lookup["environment"]
    # An inline aliases table: appending an [aliases] header to it is a duplicate table.
    _write_aliases(environment, 'aliases = { other-box = "oth" }\n')
    before = environment.read_bytes()
    code, _out, err = _allocate(_Terminal("box\n"))
    assert code == 1
    assert "would not parse" in err and "unchanged" in err
    assert environment.read_bytes() == before
    assert [entry.name for entry in environment.parent.iterdir()] == [environment.name]


def test_given_no_terminal_when_the_host_is_unconfigured_then_it_refuses_without_reading_input(real_alias_lookup):
    environment = real_alias_lookup["environment"]
    _write_aliases(environment, '[aliases]\nother-box = "oth"\n')
    before = environment.read_bytes()
    stdin = _Pipe("box\n")
    code, _out, err = _allocate(stdin, STRICT_ALIAS_FORM)
    assert code == 1
    assert "no host alias is configured" in err
    assert str(environment) in err and str(real_alias_lookup["local"]) in err
    assert "Host alias for" not in err
    assert stdin.tell() == 0
    assert environment.read_bytes() == before


def test_given_a_dry_run_in_a_terminal_when_the_host_is_unconfigured_then_it_plans_and_writes_nothing(
    real_alias_lookup,
):
    environment = real_alias_lookup["environment"]
    _write_aliases(environment, '[aliases]\nother-box = "oth"\n')
    before = environment.read_bytes()
    with patch("sys.stdin", _Terminal("box\n")):
        code, out, err = _dry_run()
    assert code == 0, err
    assert _planned_session(out) == "myrepo-box-1"
    assert "nothing was written" in err
    assert environment.read_bytes() == before
    assert not real_alias_lookup["local"].exists()


def test_given_an_invalid_registry_entry_and_a_terminal_when_launching_then_it_refuses_without_prompting(
    real_alias_lookup,
):
    _write_aliases(real_alias_lookup["environment"], '[aliases]\ntest-box = "2ec"\n')
    code, _out, err = _allocate(_Terminal("box\n"))
    assert code == 1
    assert "invalid" in err
    assert "Host alias for" not in err
