"""Regression coverage for AI-CLI-115: the idle-restart config-change watcher must
detect edits to `.claude/settings.json` and `.mcp.json`, not just `CLAUDE.md`.

Root cause of the incident this guards against: `.claude/settings.json`'s `env`
block (e.g. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) is read ONLY at Claude Code process
startup — `/compact` never re-reads it. The watcher's job is to notice such a
change and trigger an idle restart so a long-running session actually picks it
up. Before this fix, the watcher's change-detection hash covered only
`CLAUDE.md`, so a `.claude/settings.json` (or `.mcp.json`) edit was invisible to
it forever — a session could run for days past a fix with the stale config still
active.

These tests run the ACTUAL hash-computation snippet extracted from the generated
template in a real bash subprocess against a controlled temp filesystem — a
behavioral test, not a string-presence check — so a future refactor that quietly
narrows the watched-file set back down fails a real assertion, not just a grep.
"""

import re
import subprocess
import textwrap

import pytest

from ai_cli.session_script import get_engine_script


def _extract_config_watch_files_line(script: str) -> str:
    """Pull the `_config_watch_files=...` assignment out of the generated
    template so the test always exercises whatever the template actually does,
    rather than a hand-copied duplicate that could drift out of sync."""
    m = re.search(r'^\s*_config_watch_files="[^\n]*"\s*$', script, re.MULTILINE)
    assert m, "generated template no longer defines _config_watch_files — did the watcher's hash logic move?"
    return m.group(0).strip()


def _hash_via_bash(home: str, cwd: str, watch_files_line: str) -> str:
    """Run the real hash computation (same shape as the template's own baseline
    + periodic-check lines: `cat $_config_watch_files 2>/dev/null | sha256sum`)
    in an actual bash subprocess with HOME/cwd pointed at a temp fixture."""
    snippet = textwrap.dedent(f"""
        {watch_files_line}
        cat $_config_watch_files 2>/dev/null | sha256sum | cut -d' ' -f1
    """)
    result = subprocess.run(
        ["bash", "-c", snippet],
        cwd=cwd,
        env={"HOME": home, "PATH": "/usr/bin:/bin:/sbin:/usr/sbin"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def watch_files_line():
    script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", worktree_dir="/tmp/wt", project_name="myproject")
    return _extract_config_watch_files_line(script)


@pytest.fixture
def fixture_tree(tmp_path):
    """A fake $HOME/projects/CLAUDE.md + a project dir (the `$(pwd)` side) with
    its own CLAUDE.md, .claude/settings.json, and .mcp.json — mirroring the two
    real watch locations (global-under-HOME, project-under-cwd)."""
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    (home / "projects" / "CLAUDE.md").write_text("global claude md v1\n")

    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / "CLAUDE.md").write_text("project claude md v1\n")
    (project / ".claude" / "settings.json").write_text('{"env": {}}\n')
    (project / ".mcp.json").write_text('{"mcpServers": {}}\n')

    return home, project


def test_watch_files_line_covers_settings_json_and_mcp_json(watch_files_line):
    """Static contract check: the exact set of files the watcher tracks."""
    assert "$HOME/.claude/settings.json" in watch_files_line
    assert "$(pwd)/.claude/settings.json" in watch_files_line
    assert "$(pwd)/.mcp.json" in watch_files_line
    # Original CLAUDE.md coverage must not have regressed while adding the new files.
    assert "$HOME/projects/CLAUDE.md" in watch_files_line
    assert "$(pwd)/CLAUDE.md" in watch_files_line


def test_editing_project_settings_json_changes_the_watch_hash(watch_files_line, fixture_tree):
    """The core regression: before this fix, editing .claude/settings.json's `env`
    block was invisible to the watcher (identical hash before/after) — the exact
    condition that let CLAUDE_AUTOCOMPACT_PCT_OVERRIDE stay silently stale for
    2+ days. This must now change the hash."""
    home, project = fixture_tree
    before = _hash_via_bash(str(home), str(project), watch_files_line)

    (project / ".claude" / "settings.json").write_text('{"env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "70"}}\n')
    after = _hash_via_bash(str(home), str(project), watch_files_line)

    assert before != after


def test_editing_global_settings_json_changes_the_watch_hash(watch_files_line, fixture_tree):
    """Same coverage for the global `~/.claude/settings.json` side, not just the
    project-level file."""
    home, project = fixture_tree
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"env": {}}\n')
    before = _hash_via_bash(str(home), str(project), watch_files_line)

    (home / ".claude" / "settings.json").write_text('{"env": {"SOME_KEY": "1"}}\n')
    after = _hash_via_bash(str(home), str(project), watch_files_line)

    assert before != after


def test_editing_mcp_json_changes_the_watch_hash(watch_files_line, fixture_tree):
    """.mcp.json is read at CC startup the same way .claude/settings.json is —
    same staleness risk, same fix."""
    home, project = fixture_tree
    before = _hash_via_bash(str(home), str(project), watch_files_line)

    (project / ".mcp.json").write_text('{"mcpServers": {"added": {}}}\n')
    after = _hash_via_bash(str(home), str(project), watch_files_line)

    assert before != after


def test_editing_claude_md_still_changes_the_watch_hash(watch_files_line, fixture_tree):
    """Pre-existing behavior must not have regressed while extending the watch set."""
    home, project = fixture_tree
    before = _hash_via_bash(str(home), str(project), watch_files_line)

    (project / "CLAUDE.md").write_text("project claude md v2\n")
    after = _hash_via_bash(str(home), str(project), watch_files_line)

    assert before != after


def test_hash_is_stable_when_nothing_changes(watch_files_line, fixture_tree):
    """Sanity check on the fixture/harness itself — no spurious hash churn."""
    home, project = fixture_tree
    first = _hash_via_bash(str(home), str(project), watch_files_line)
    second = _hash_via_bash(str(home), str(project), watch_files_line)
    assert first == second
