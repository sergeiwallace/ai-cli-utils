"""Coverage for the unconditional pre-launch settings override in the generated
launch template: on every Claude Code launch, the template writes a specific
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
import re
import subprocess
import textwrap

import pytest

from ai_cli.session_script import get_engine_script

SETTINGS_RELPATH = ".claude/settings.local.json"


def _extract_toggle_block(script: str) -> str:
    """Pull the `if [[ "$engine" == "c" ]]; then ...` growthbook-override block
    out of the generated template so the test always exercises whatever the
    template actually does, rather than a hand-copied duplicate that could
    drift out of sync.

    The template has several `if [[ "$engine" == "c" ]]; then` conditionals at
    different indentation levels; anchor on the growthbook block's specific
    6-space indent (and its `python3 -c` body) to select the right one."""
    m = re.search(
        r'^ {6}if \[\[ "\$engine" == "c" \]\]; then\n {8}python3 -c "\n(?:.*\n)*? {6}fi\n',
        script,
        re.MULTILINE,
    )
    assert m, "generated template no longer defines the growthbook launch override — did it move or get renamed?"
    return m.group(0)


def _run_toggle_block(tmp_path, engine: str, block: str) -> subprocess.CompletedProcess:
    snippet = textwrap.dedent(f'engine="{engine}"\n') + block
    return subprocess.run(
        ["bash", "-c", snippet],
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin:/sbin:/usr/sbin"},
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.fixture
def toggle_block():
    script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", worktree_dir="/tmp/wt", project_name="sergei")
    return _extract_toggle_block(script)


def test_given_engine_c_when_toggle_runs_then_settings_local_json_always_gets_the_override(tmp_path, toggle_block):
    (tmp_path / ".claude").mkdir()

    result = _run_toggle_block(tmp_path, "c", toggle_block)

    assert result.returncode == 0, result.stderr
    settings_path = tmp_path / SETTINGS_RELPATH
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert data["env"]["DISABLE_GROWTHBOOK"] == ""


def test_given_existing_settings_local_json_when_toggle_runs_then_other_keys_are_preserved(tmp_path, toggle_block):
    (tmp_path / ".claude").mkdir()
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.write_text(json.dumps({"env": {"OTHER_VAR": "keep-me"}, "hooks": {"foo": "bar"}}))

    result = _run_toggle_block(tmp_path, "c", toggle_block)

    assert result.returncode == 0, result.stderr
    data = json.loads(settings_path.read_text())
    assert data["env"]["OTHER_VAR"] == "keep-me"
    assert data["env"]["DISABLE_GROWTHBOOK"] == ""
    assert data["hooks"] == {"foo": "bar"}


def test_given_gemini_engine_when_toggle_runs_then_no_write_happens(tmp_path, toggle_block):
    (tmp_path / ".claude").mkdir()

    result = _run_toggle_block(tmp_path, "g", toggle_block)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / SETTINGS_RELPATH).exists()


def test_given_malformed_existing_settings_local_json_when_toggle_runs_then_it_recovers_rather_than_crashing(
    tmp_path, toggle_block
):
    (tmp_path / ".claude").mkdir()
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.write_text("{not valid json")

    result = _run_toggle_block(tmp_path, "c", toggle_block)

    assert result.returncode == 0, result.stderr
    data = json.loads(settings_path.read_text())
    assert data["env"]["DISABLE_GROWTHBOOK"] == ""
