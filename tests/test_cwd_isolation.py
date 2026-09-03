"""Regression coverage for test-process working-directory isolation."""

import os
import shutil
from pathlib import Path


def test_given_cli_changes_to_deleted_directory_when_test_ends_then_next_test_starts_at_checkout(tmp_path):
    """Model a CLI invocation that enters a temporary project directory."""
    os.chdir(tmp_path)
    shutil.rmtree(tmp_path)


def test_given_previous_test_deleted_cwd_when_path_cwd_called_then_returns_checkout():
    """A later test must not inherit a removed working directory."""
    checkout = Path(__file__).resolve().parent.parent
    try:
        assert Path.cwd() == checkout
    finally:
        os.chdir(checkout)
