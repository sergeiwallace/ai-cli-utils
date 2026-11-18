import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
STAMP = (
    "test_session_self_update.py::"
    "test_given_a_stamp_under_the_xdg_state_home_when_a_template_is_generated_then_it_bakes_that_commit"
)
MIGRATION = (
    "test_session_migrate.py::test_given_a_registered_slot_when_migrated_then_every_artifact_follows_the_new_name"
)


@pytest.fixture
def child_environment(tmp_path):
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    for key in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "CLAUDE_CONFIG_DIR"):
        directory = tmp_path / key.lower()
        directory.mkdir()
        env[key] = str(directory)
    env["USERPROFILE"] = env["HOME"]
    return env


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a POSIX account home via the password database")
@pytest.mark.parametrize("variable", ["HOME", "XDG_STATE_HOME"])
def test_given_live_state_and_no_session_fixture_when_pytest_starts_then_collection_is_refused(
    tmp_path, child_environment, variable
):
    account_home = tmp_path / "account"
    account_home.mkdir()
    unsafe_path = account_home if variable == "HOME" else account_home / ".local" / "state"
    unsafe_path.mkdir(parents=True, exist_ok=True)
    child_environment[variable] = str(unsafe_path)

    # Load the real conftest as a plugin with just the session fixture disabled.
    source = (TESTS / "conftest.py").read_text(encoding="utf-8")
    source = source.replace(
        '@pytest.fixture(scope="session", autouse=True)\ndef _verify_user_state_isolation',
        "def _verify_user_state_isolation",
    )
    (tmp_path / "conftest.py").write_text(source, encoding="utf-8")
    launcher = (
        "import pwd, sys, types, pytest\n"
        "from unittest.mock import patch\n"
        "with patch.object(pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_dir=sys.argv[1])):\n"
        "    raise SystemExit(pytest.main(sys.argv[2:]))\n"
    )
    child_environment["PYTHONPATH"] = os.pathsep.join(map(str, (tmp_path, TESTS, TESTS.parent / "src")))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            launcher,
            str(account_home),
            "--noconftest",
            "-p",
            "conftest",
            "-n",
            "0",
            "-q",
            str(TESTS / STAMP),
            str(TESTS / MIGRATION),
        ],
        cwd=tmp_path,
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert f"{variable} points inside the real home" in result.stderr
    assert sorted(account_home.rglob("*")) == ([] if variable == "HOME" else [unsafe_path.parent, unsafe_path])


def test_given_a_safe_startup_home_when_pytest_collects_then_imports_already_use_private_state(
    tmp_path, child_environment
):
    probe = tmp_path / "test_collection_probe.py"
    probe.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from ai_cli import cc_usage, config\n"
        "home = Path.home()\n"
        "state = config.get_xdg_state_home()\n"
        "assert home != Path(os.environ['STARTUP_HOME'])\n"
        "assert home.parent == state.parent.parent\n"
        "assert cc_usage._CLAUDE_PROJECTS_DIR.is_relative_to(home)\n"
        "assert cc_usage._STATE_DIR.is_relative_to(home.parent)\n"
        "assert Path(os.environ['CLAUDE_CONFIG_DIR']).is_relative_to(home.parent)\n"
        "def test_probe():\n"
        "    assert Path.home().is_dir()\n",
        encoding="utf-8",
    )
    child_environment["STARTUP_HOME"] = child_environment["HOME"]
    child_environment["PYTHONPATH"] = str(TESTS)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-n", "0", "-p", "conftest", "-q", str(probe)],
        cwd=TESTS.parent,
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    assert "1 passed" in result.stdout
