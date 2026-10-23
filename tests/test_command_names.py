"""Leaf commands are named {verb}-{noun}; the {noun}-{verb} names they replaced are gone (AI-CLI-bcu3)."""

import pytest
from conftest import run_cli

RENAMED = [
    ("session-migrate", "migrate-session"),
    ("session-adopt", "adopt-session"),
    ("session-retire", "retire-session"),
    ("session-audit", "audit-sessions"),
    ("cc-migrate", "migrate-transcript"),
    ("trust-backfill", "backfill-trust"),
    ("vpn-watch", "watch-vpn"),
]


@pytest.mark.parametrize(("old", "new"), RENAMED)
def test_given_renamed_command_when_invoked_by_new_name_then_it_resolves(old, new):
    exit_code, stdout, stderr = run_cli(["ai", new, "--help"])

    assert exit_code == 0, stderr
    assert f"Usage: ai {new} " in stdout


@pytest.mark.parametrize(("old", "new"), RENAMED)
def test_given_renamed_command_when_invoked_by_old_name_then_it_is_an_unknown_command(old, new):
    exit_code, _, stderr = run_cli(["ai", old, "--help"])

    assert exit_code == 1
    assert f"No such command '{old}'" in stderr


def test_given_top_level_help_when_listed_then_only_the_new_names_appear():
    exit_code, stdout, stderr = run_cli(["ai", "--help"])

    assert exit_code == 0, stderr
    listed = {line.split()[0] for line in stdout.splitlines() if line.startswith("  ") and line.split()}
    for old, new in RENAMED:
        assert new in listed
        assert old not in listed
