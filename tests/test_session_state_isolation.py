"""Exercise the stateful tests through pytest with and without its conftest."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

TARGETS = (
    "test_session_self_update.py::"
    "test_given_a_stamp_under_the_xdg_state_home_when_a_template_is_generated_then_it_bakes_that_commit",
    "test_session_migrate_on_launch.py::"
    "test_given_a_recorded_unfinished_migration_when_new_is_launched_then_it_is_finished_first",
)


@pytest.mark.parametrize("target", TARGETS, ids=("update-stamp", "migration-ledger"))
@pytest.mark.parametrize("with_conftest", [True, False], ids=("normal", "no-conftest"))
def test_given_stateful_session_tests_when_pytest_runs_then_isolation_is_required(
    tmp_path_factory, target, with_conftest
):
    root = tmp_path_factory.mktemp("iso").resolve()
    env = os.environ.copy()
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST"):
        env.pop(name, None)
    locations = {
        "HOME": "home",
        "USERPROFILE": "home",
        "XDG_STATE_HOME": "state",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data",
        "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata",
    }
    for name, subdir in locations.items():
        path = root / subdir
        path.mkdir(exist_ok=True)
        env[name] = str(path)
        under_root = Path(env[name]).resolve().is_relative_to(root)
        print(f"{name} under temporary root: {under_root}")
        assert under_root, name
    env["AI_CLI_CANONICAL_WORKTREE_REGISTRY"] = str(root / "registry.json")
    cwd = Path(__file__).resolve().parents[1]
    args = ["-q", "--tb=line", "-o", "addopts=", "--basetemp", str(root / "pytest")]
    if not with_conftest:
        args.append("--noconftest")
        if target.startswith("test_session_migrate_"):
            # The unsafe slot setup runs even in this mode. Keep the control run
            # from driving the CLI after setup when the isolation check is absent.
            args.append("--setup-only")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *args, f"tests/{target}"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    output = result.stdout + result.stderr

    if with_conftest:
        assert result.returncode == pytest.ExitCode.OK, output
        assert "1 passed" in output
    else:
        assert result.returncode == pytest.ExitCode.TESTS_FAILED, output
        assert "Session tests require isolated HOME and XDG directories" in output
        assert all(not any((root / subdir).iterdir()) for subdir in locations.values())
        assert not (root / "registry.json").exists()
