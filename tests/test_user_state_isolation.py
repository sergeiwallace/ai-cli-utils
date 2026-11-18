import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


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


@pytest.fixture
def collection_probe(tmp_path):
    probe = tmp_path / "test_collection_probe.py"
    probe.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from ai_cli import cc_usage, config\n"
        "home = Path.home()\n"
        "state = config.get_xdg_state_home()\n"
        "assert home != Path(os.environ['STARTUP_HOME'])\n"
        "assert home == Path(os.environ['HOME']) == Path(os.environ['USERPROFILE'])\n"
        "assert home.parent == state.parent.parent\n"
        "assert cc_usage._CLAUDE_PROJECTS_DIR.is_relative_to(home)\n"
        "assert cc_usage._STATE_DIR.is_relative_to(home.parent)\n"
        "assert Path(os.environ['CLAUDE_CONFIG_DIR']).is_relative_to(home.parent)\n"
        "assert Path(os.environ['XDG_CONFIG_HOME']).is_relative_to(home.parent)\n"
        "assert Path(os.environ['XDG_DATA_HOME']).is_relative_to(home.parent)\n"
        "outside = Path(os.environ['STARTUP_HOME'])\n"
        "if os.environ.get('ISOLATION_PROBE_ESCAPE') == 'home':\n"
        "    Path.home = classmethod(lambda cls: outside)\n"
        "elif os.environ.get('ISOLATION_PROBE_ESCAPE') == 'state':\n"
        "    config.get_xdg_state_home = lambda: outside\n"
        "def test_probe():\n"
        "    assert Path.home().is_dir()\n",
        encoding="utf-8",
    )
    return probe


@pytest.mark.skipif(sys.platform == "win32", reason="simulates a POSIX account home via the password database")
@pytest.mark.parametrize("variable", ["HOME", "XDG_STATE_HOME"])
def test_given_account_home_paths_when_pytest_starts_then_collection_is_redirected(
    tmp_path, child_environment, collection_probe, variable
):
    account_home = tmp_path / "account"
    account_home.mkdir()
    child_environment[variable] = str(account_home if variable == "HOME" else account_home / ".local" / "state")
    child_environment["STARTUP_HOME"] = child_environment["HOME"]

    launcher = (
        "import pwd, sys, types, pytest\n"
        "from unittest.mock import patch\n"
        "with patch.object(pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_dir=sys.argv[1])):\n"
        "    raise SystemExit(pytest.main(sys.argv[2:]))\n"
    )
    child_environment["PYTHONPATH"] = os.pathsep.join(map(str, (TESTS, TESTS.parent / "src")))
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
            str(collection_probe),
        ],
        cwd=tmp_path,
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert list(account_home.iterdir()) == []


@pytest.mark.parametrize(
    ("escape", "diagnostic"),
    [
        ("", "1 passed"),
        ("home", "Path.home() resolved outside the isolation directory."),
        ("state", "get_xdg_state_home() resolved outside the isolation directory."),
    ],
)
def test_given_a_safe_startup_home_when_pytest_collects_then_imports_already_use_private_state(
    child_environment, collection_probe, escape, diagnostic
):
    child_environment["STARTUP_HOME"] = child_environment["HOME"]
    child_environment["ISOLATION_PROBE_ESCAPE"] = escape
    child_environment["PYTHONPATH"] = str(TESTS)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-n", "0", "-p", "conftest", "-q", str(collection_probe)],
        cwd=TESTS.parent,
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    expected = pytest.ExitCode.USAGE_ERROR if escape else pytest.ExitCode.OK
    assert result.returncode == expected, result.stdout + result.stderr
    assert diagnostic in result.stdout + result.stderr
    if escape:
        assert "1 passed" not in result.stdout


def test_given_the_conftest_is_imported_a_second_time_when_it_loads_then_it_reuses_the_session_tree() -> None:
    import importlib.util

    from tests import conftest as session_conftest

    spec = importlib.util.spec_from_file_location("second_copy_of_conftest", Path(session_conftest.__file__))
    assert spec is not None and spec.loader is not None
    second = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(second)

    assert second._SESSION_STATE_ROOT == session_conftest._SESSION_STATE_ROOT
    assert second._SESSION_STATE_DIRECTORY is session_conftest._SESSION_STATE_DIRECTORY
