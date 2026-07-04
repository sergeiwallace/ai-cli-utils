"""Tests for the session template self-update / stable-script regeneration path.

Regression coverage for the task-namespace-loss bug: a running wrapper failed to
pick up an installed fix because the self-update trigger keyed off a non-monotonic
package version and permanently gave up after a single refresh failure. The fixes:

  * generated templates export ``CLAUDE_CODE_TASK_LIST_ID`` (stable task namespace)
  * self-update triggers on the monotonic ``last_update_commit.txt`` stamp
  * a refresh failure does NOT advance the baked version/commit (retries next restart)
  * ``ai update`` regenerates every live session's stable script (mtime hot-reload)

These assert the behavioral contract of the generated bash + the regen helper.
"""

import json

from ai_cli.main import _write_stable_session_script
from ai_cli.session_script import get_engine_script


def _script(engine="c", **kw):
    kw.setdefault("worktree_dir", "/tmp/wt")
    kw.setdefault("project_name", "job-pilot")
    return get_engine_script(engine, "job-1", "c-job-1", "c-job-", "job", **kw)


def test_task_list_namespace_pin_is_guarded_to_claude_engine():
    """The CLAUDE_CODE_TASK_LIST_ID export is runtime-guarded to CC (`engine == c`)
    so it pins the task namespace for Claude sessions but never for Gemini ones.
    The literal is present in both templates; the guard is what makes it CC-only."""
    guarded = '[[ "$engine" == "c" ]] && export CLAUDE_CODE_TASK_LIST_ID="$ai_name"'
    cc = _script("c")
    gg = get_engine_script("g", "job-1", "g-job-1", "g-job-", "job", worktree_dir="/tmp/wt")
    assert guarded in cc and 'engine="c"' in cc
    # Same guarded line in the Gemini template, but engine="g" makes it a no-op at runtime.
    assert guarded in gg and 'engine="g"' in gg
    # The export must never appear unguarded (which would set it for Gemini too).
    assert "export CLAUDE_CODE_TASK_LIST_ID" not in gg.replace(guarded, "")


def test_template_bakes_update_commit_for_monotonic_self_update():
    """The template bakes _template_commit and compares it against the live stamp —
    the monotonic update signal that replaces the flaky version-only trigger."""
    s = _script("c")
    assert "_template_commit=" in s
    assert 'cat "$_ai_state_dir/last_update_commit.txt"' in s


def test_self_update_does_not_permanently_give_up_on_refresh_failure():
    """The old code set _template_version=$_current_ver on refresh failure, which
    permanently disabled self-update. That give-up assignment must be gone."""
    s = _script("c")
    assert '_template_version="$_current_ver"' not in s
    # Failure path retries rather than swallowing the update signal.
    assert "will retry on next restart" in s


def test_write_stable_script_returns_false_without_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert _write_stable_session_script("c-nope-1") is False


def test_write_stable_script_regenerates_from_metadata(monkeypatch, tmp_path):
    """Given a persisted session-meta, the regen writes an executable stable script
    that carries the session's identity — this is what bumps mtime for hot-reload."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state = tmp_path / "ai-cli-utils"
    state.mkdir(parents=True)
    meta = {
        "engine": "c",
        "ai_name": "job-1",
        "session": "c-job-1",
        "prefix": "c-job-",
        "project_prefix": "job",
        "worktree_dir": "/tmp/wt",
        "project_name": "job-pilot",
    }
    (state / "session-meta-c-job-1.json").write_text(json.dumps(meta))

    assert _write_stable_session_script("c-job-1") is True
    out = state / "sessions" / "c-job-1.sh"
    assert out.exists()
    body = out.read_text()
    assert 'ai_name="job-1"' in body
    assert 'CLAUDE_CODE_TASK_LIST_ID="$ai_name"' in body
