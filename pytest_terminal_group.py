"""Keep real terminal tests from competing with one another on macOS."""

import sys

import pytest


def pytest_load_initial_conftests(args: list[str]) -> None:
    if sys.platform == "darwin":
        # Set the default before parsing in both the controller and workers.
        # Later CLI options can still choose another scheduler.
        args[:0] = ["--dist", "loadgroup"]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if sys.platform == "darwin":
        for item in items:
            if item.get_closest_marker("real_tmux") or item.get_closest_marker("real_pty"):
                item.add_marker(pytest.mark.xdist_group("real-terminal"))
