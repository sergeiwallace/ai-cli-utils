"""Coverage for the opt-in pre-launch settings override in the generated launch
template: when a worktree carries a marker file, the template writes a specific
`env` override into `.claude/settings.local.json` before the agent process
starts, on every launch and every restart.

Why this exists: some Claude Code feature checks resolve once, very early in
process startup — before any same-process settings change (e.g. one written by
a SessionStart hook after the process has already begun) can influence them.
Live testing confirmed a value already present in `.claude/settings.local.json`
before the process launches is observed correctly, while the identical value
written by a same-process hook mid-startup is not. Writing the override here,
ahead of every `run_agent claude` invocation, sidesteps that ordering question
entirely instead of trying to win a race against it.

These tests run the ACTUAL snippet extracted from the generated template in a
real bash + python3 subprocess against a controlled temp filesystem — a
behavioral test, not a string-presence check — matching the convention in
`test_config_watch_hash.py`.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ai_cli.session_script import get_engine_script

MARKER_RELPATH = ".claude/.ai-cli-growthbook-toggle"
SETTINGS_RELPATH = ".claude/settings.local.json"


def _minimal_path_with_python3() -> str:
    """A deliberately minimal PATH that still contains a real ``python3``.

    A hardcoded ``/usr/bin:/bin:/sbin:/usr/sbin`` assumes a distro layout. On a
    conda-based image ``python3`` lives in ``/opt/conda/bin`` and nothing is at
    ``/usr/bin/python3``, so the template's ``python3 -c … 2>/dev/null || true`` silently
    did nothing and the assertions failed while the code under test was fine. Derive the
    directory from the running interpreter instead of guessing at it.
    """
    interpreter_dir = str(Path(sys.executable).parent)
    return os.pathsep.join([interpreter_dir, "/usr/bin", "/bin", "/sbin", "/usr/sbin"])


def _extract_toggle_block(script: str) -> str:
    """Pull the `if [[ "$engine" == "c" && -f ".claude/.ai-cli-growthbook-toggle" ...`
    block out of the generated template so the test always exercises whatever
    the template actually does, rather than a hand-copied duplicate that could
    drift out of sync."""
    m = re.search(
        r'if \[\[ "\$engine" == "c" && -f "\.claude/\.ai-cli-growthbook-toggle" \]\]; then\n(?:.*\n)*?      fi\n',
        script,
    )
    assert m, "generated template no longer defines the growthbook launch toggle — did it move or get renamed?"
    return m.group(0)


def _run_toggle_block(tmp_path, engine: str, block: str) -> subprocess.CompletedProcess:
    snippet = textwrap.dedent(f'engine="{engine}"\n') + block
    return subprocess.run(
        ["bash", "-c", snippet],
        cwd=str(tmp_path),
        env={"PATH": _minimal_path_with_python3()},
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.fixture
def toggle_block():
    script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", worktree_dir="/tmp/wt", project_name="myproject")
    return _extract_toggle_block(script)


def test_given_marker_present_when_toggle_runs_then_settings_local_json_gets_the_override(tmp_path, toggle_block):
    (tmp_path / ".claude").mkdir()
    (tmp_path / MARKER_RELPATH).touch()

    result = _run_toggle_block(tmp_path, "c", toggle_block)

    assert result.returncode == 0, result.stderr
    settings_path = tmp_path / SETTINGS_RELPATH
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert data["env"]["DISABLE_GROWTHBOOK"] == ""


def test_given_existing_settings_local_json_when_toggle_runs_then_other_keys_are_preserved(tmp_path, toggle_block):
    (tmp_path / ".claude").mkdir()
    (tmp_path / MARKER_RELPATH).touch()
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.write_text(json.dumps({"env": {"OTHER_VAR": "keep-me"}, "hooks": {"foo": "bar"}}))

    result = _run_toggle_block(tmp_path, "c", toggle_block)

    assert result.returncode == 0, result.stderr
    data = json.loads(settings_path.read_text())
    assert data["env"]["OTHER_VAR"] == "keep-me"
    assert data["env"]["DISABLE_GROWTHBOOK"] == ""
    assert data["hooks"] == {"foo": "bar"}


def test_given_no_marker_when_toggle_runs_then_settings_local_json_is_not_created(tmp_path, toggle_block):
    (tmp_path / ".claude").mkdir()

    result = _run_toggle_block(tmp_path, "c", toggle_block)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / SETTINGS_RELPATH).exists()


def test_given_gemini_engine_when_toggle_runs_even_with_marker_present_then_no_write_happens(tmp_path, toggle_block):
    (tmp_path / ".claude").mkdir()
    (tmp_path / MARKER_RELPATH).touch()

    result = _run_toggle_block(tmp_path, "g", toggle_block)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / SETTINGS_RELPATH).exists()


def test_given_malformed_existing_settings_local_json_when_toggle_runs_then_it_recovers_rather_than_crashing(
    tmp_path, toggle_block
):
    (tmp_path / ".claude").mkdir()
    (tmp_path / MARKER_RELPATH).touch()
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.write_text("{not valid json")

    result = _run_toggle_block(tmp_path, "c", toggle_block)

    assert result.returncode == 0, result.stderr
    data = json.loads(settings_path.read_text())
    assert data["env"]["DISABLE_GROWTHBOOK"] == ""
