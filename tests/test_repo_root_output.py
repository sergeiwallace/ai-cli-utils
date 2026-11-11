"""Repository discovery cannot infer an owner from missing Git output."""

import subprocess

from ai_cli.session import detect_repo_root


def test_given_empty_successful_git_output_when_discovering_repo_then_no_owner_is_invented(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    assert detect_repo_root() is None
