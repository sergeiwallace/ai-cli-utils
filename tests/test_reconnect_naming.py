"""Reconnect suggestions preserve the complete remote session slot."""

import subprocess
from unittest.mock import patch

import pytest
from conftest import run_cli


@pytest.mark.parametrize("title", ["myrepo-3", "myrepo-box-review-3", "myrepo-box-cleo"])
def test_given_named_remote_session_when_reconnect_lists_then_full_slot_and_project_are_preserved(title):
    with (
        patch("ai_cli.main.subprocess.run", return_value=subprocess.CompletedProcess([], 0, f"c-r-{title}\n")),
        patch("ai_cli.config.get_project_aliases", return_value={"myrepo": "myproject"}),
    ):
        code, out, err = run_cli(["ai", "reconnect"], config={"remote": {"host": "example.com", "user": "user"}})
    assert code == 0, err
    assert f"ai c c-r-{title} -R -p myrepo" in out
