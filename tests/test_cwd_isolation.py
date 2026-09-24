"""Regression coverage for test-process working-directory isolation."""

import os
import shutil
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows holds a handle to every process's current directory, so a deleted cwd is unreachable",
)
def test_given_cli_changes_to_deleted_directory_when_test_ends_then_next_test_starts_at_checkout(tmp_path):
    """Model a CLI invocation that enters a temporary project directory.

    Deleting the directory the process is currently in is a POSIX-only move: the
    directory entry goes away while the process keeps its reference to the unlinked
    inode. Windows keeps an open handle to each process's current directory, so
    ``rmtree`` there raises ``PermissionError: [WinError 32]`` and the *precondition*
    this guard exists for cannot be created -- which also means the isolation defect
    it protects against cannot occur on that platform. Skipped because the OS makes
    the scenario impossible, not to get past a red test.
    """
    os.chdir(tmp_path)
    shutil.rmtree(tmp_path)


def test_given_previous_test_deleted_cwd_when_path_cwd_called_then_returns_checkout():
    """A later test must not inherit a removed working directory."""
    checkout = Path(__file__).resolve().parent.parent
    try:
        assert Path.cwd() == checkout
    finally:
        os.chdir(checkout)
