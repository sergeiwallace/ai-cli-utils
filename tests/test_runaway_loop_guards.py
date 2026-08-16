"""Runaway-loop regression coverage (AI-CLI-129).

Two independent unbounded paths are pinned here:

1. The session watcher's poll loop. It is documented and tested elsewhere as a
   one-tick-per-second loop ("skip the first 10 watcher cycles (10s)"), and every
   tick can run ``tmux capture-pane`` / ``ai internal publish-heartbeat`` /
   ``sha256sum`` subprocesses. If its pacing statement does not actually block,
   the loop busy-spins and those become a subprocess storm.

2. ``ai update``'s live-session template refresh. It runs a ``tmux list-sessions``
   subprocess plus a file write and a chmod per live session, with nothing
   stopping a pathological caller from re-entering it in a loop.

Both are asserted behaviourally: run the real loop body and count its ticks in a
fixed wall-clock window; drive the real refresh function and count the work it
actually does.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import ai_cli.main as main
from ai_cli.main import _refresh_live_session_scripts, _write_stable_session_script
from ai_cli.session_script import get_engine_script

# --- Watcher poll loop ---------------------------------------------------------

_WATCH_WINDOW_SECONDS = 2
# The loop is specified as one tick per second. Two ticks fit the window; allow
# double that for scheduler slop. A busy-spin overshoots this by 2+ orders.
_MAX_TICKS = 4


def _watcher_loop_body() -> str:
    """The bash body of the watcher's ``while true`` loop, verbatim from the template."""
    script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", worktree_dir="/tmp/wt", project_name="myproject")
    watcher_start = script.index("start_watcher() {")
    body_start = script.index("while true; do", watcher_start) + len("while true; do")
    body_end = script.index("done) &", body_start)
    return script[body_start:body_end]


def _run_watcher_loop(tmp_path: Path) -> int:
    """Run the real watcher loop body for a fixed window; return the tick count.

    External commands the body reaches for are stubbed as instant no-ops so the
    measurement reflects the loop's own pacing, not process-spawn latency, and so
    no live tmux/ai process is touched.
    """
    shell = shutil.which("zsh") or shutil.which("bash")
    if shell is None:  # pragma: no cover - CI always has one of these
        pytest.skip("no zsh/bash available to run the watcher loop")

    watched = tmp_path / "watched.json"
    watched.write_text("{}")

    harness = tmp_path / "watcher_harness.sh"
    harness.write_text(
        f"""
tmux_session="c-sw-1"
ai_name="sw-1"
engine="c"
project_prefix="sw"
signal_file="{tmp_path}/signal"
config_hash_file="{tmp_path}/config-hash"
config_changed_file="{tmp_path}/config-changed"
reload_file="{tmp_path}/reload"
restart_file="{tmp_path}/restart"
_config_watch_files="{watched}"
_config_reload_idle_secs=90
counter=0
ticks=0
SECONDS=0
# Shell functions shadow the external commands the watcher invokes.  This is
# more reliable than prepending a native Windows path to PATH before MSYS2
# Bash interprets it, and still exercises the real watcher loop and its sleep.
tmux() {{ :; }}
ai() {{ :; }}
sha256sum() {{ :; }}
while true; do
{_watcher_loop_body()}
  ticks=$((ticks+1))
  (( SECONDS >= {_WATCH_WINDOW_SECONDS} )) && break
done
echo "TICKS=$ticks"
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [shell, str(harness)],
        capture_output=True,
        text=True,
        timeout=_WATCH_WINDOW_SECONDS + 60,
        check=False,
    )
    ticks_line = [ln for ln in result.stdout.splitlines() if ln.startswith("TICKS=")]
    assert ticks_line, f"harness produced no tick count: {result.stdout!r} {result.stderr!r}"
    return int(ticks_line[-1].split("=", 1)[1])


def test_given_idle_session_watcher_when_it_polls_then_it_ticks_at_most_once_per_second(tmp_path):
    """An idle watcher must tick about once per second, not spin.

    Every tick can fire ``tmux capture-pane``, ``ai internal publish-heartbeat``
    and a ``sha256sum`` pipeline, and the loop's grace periods are counted in
    ticks ("counter >= 10" == 10 seconds). A tick rate above ~1 Hz means the loop
    is a subprocess storm and every duration derived from ``counter`` is wrong.
    """
    ticks = _run_watcher_loop(tmp_path)
    assert ticks <= _MAX_TICKS, (
        f"watcher ticked {ticks} times in {_WATCH_WINDOW_SECONDS}s "
        f"(expected <= {_MAX_TICKS}) — the poll loop is busy-spinning"
    )


# --- `ai update` live-template refresh ----------------------------------------


def _install_session_meta(state: Path, tmux_session: str, ai_name: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / f"session-meta-{tmux_session}.json").write_text(
        json.dumps(
            {
                "engine": "c",
                "ai_name": ai_name,
                "session": tmux_session,
                "prefix": "c-sw-",
                "project_prefix": "sw",
                "worktree_dir": "/tmp/wt",
                "project_name": "myproject",
            }
        )
    )


class _FakeTmux:
    """Stand-in for ``tmux list-sessions``; counts how often it is spawned."""

    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = 0

    def __call__(self, cmd, *args, **kwargs):
        self.calls += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(self.sessions) + "\n", stderr="")


@pytest.fixture
def refresh_env(monkeypatch, tmp_path):
    """Isolated state dir with one live ai-cli session, plus a counting fake tmux."""
    state = tmp_path / "ai-cli-utils"
    monkeypatch.setattr("ai_cli.config.get_xdg_state_home", lambda: state)
    monkeypatch.setattr(main, "_REFRESH_CALL_TIMES", [], raising=False)
    monkeypatch.setattr(main, "_REFRESH_BURST_REPORTED_AT", 0.0, raising=False)
    _install_session_meta(state, "c-sw-1", "sw-1")
    fake_tmux = _FakeTmux(["c-sw-1", "c-other-9"])
    with patch.object(main.subprocess, "run", fake_tmux):
        yield state, fake_tmux


def test_given_windows_when_unchanged_template_refresh_reruns_then_script_is_not_rewritten(refresh_env, monkeypatch):
    """A refresh that would regenerate identical bytes must not touch the file.

    The stable script's mtime is the hot-reload signal every live wrapper watches,
    so rewriting an unchanged script makes every session exec a reload for nothing.
    """
    state, _ = refresh_env
    monkeypatch.setattr(main.os, "name", "nt")

    assert _refresh_live_session_scripts() == 1
    script_path = state / "sessions" / "c-sw-1.sh"
    first_mtime = script_path.stat().st_mtime_ns

    assert _refresh_live_session_scripts() == 0
    assert script_path.stat().st_mtime_ns == first_mtime


def test_given_changed_template_when_refresh_runs_then_script_is_rewritten(refresh_env):
    """Idempotence must not suppress a real update — changed content still lands."""
    state, _ = refresh_env
    _refresh_live_session_scripts()
    script_path = state / "sessions" / "c-sw-1.sh"
    script_path.write_text("#!/bin/zsh\n# stale content from an older template\n", encoding="utf-8")

    assert _refresh_live_session_scripts() == 1
    assert "stale content" not in script_path.read_text(encoding="utf-8")
    # Windows' chmod() only toggles the read-only attribute -- it cannot express
    # POSIX owner/group/other bits, so st_mode never reports 0o700 there.
    if sys.platform != "win32":
        assert script_path.stat().st_mode & 0o777 == 0o700


def test_given_refresh_called_in_a_storm_then_it_stops_and_reports_the_caller(refresh_env, capsys):
    """A caller re-entering the refresh in a loop must be cut off, loudly.

    AI-CLI-129: the refresh runs a tmux subprocess plus a write and a chmod per
    live session, so an unbounded caller turns it into a subprocess storm. The
    refresh must bound its own work and say why it stopped rather than spin.
    """
    _, fake_tmux = refresh_env
    attempts = main._REFRESH_BURST_LIMIT * 5

    for _ in range(attempts):
        _refresh_live_session_scripts()

    assert fake_tmux.calls <= main._REFRESH_BURST_LIMIT, (
        f"{fake_tmux.calls} tmux subprocesses for {attempts} refresh attempts — the refresh is not bounded"
    )
    err = capsys.readouterr().err
    assert "refresh" in err.lower()
    assert str(main._REFRESH_BURST_LIMIT) in err
    # The complaint itself must not become the storm.
    assert err.lower().count("refusing to run") == 1


def test_given_refresh_budget_is_spent_when_caller_keeps_spinning_then_rejections_do_not_rewrite_state(
    refresh_env, monkeypatch
):
    """After the bound trips, a runaway must not merely move its writes to the counter."""
    state, fake_tmux = refresh_env
    for _ in range(main._REFRESH_BURST_LIMIT):
        _refresh_live_session_scripts()

    calls_path = state / "refresh-template-calls.json"
    original_write_text = Path.write_text
    counter_writes = 0

    def count_counter_writes(path, *args, **kwargs):
        nonlocal counter_writes
        if path == calls_path:
            counter_writes += 1
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", count_counter_writes)
    for _ in range(main._REFRESH_BURST_LIMIT * 5):
        _refresh_live_session_scripts()

    assert fake_tmux.calls == main._REFRESH_BURST_LIMIT
    assert counter_writes == 0


def test_given_no_metadata_when_writing_stable_script_then_it_reports_failure(monkeypatch, tmp_path):
    """The unchanged-script short circuit must not mask a genuinely missing session."""
    monkeypatch.setattr("ai_cli.config.get_xdg_state_home", lambda: tmp_path / "ai-cli-utils")
    monkeypatch.setattr(main, "_REFRESH_CALL_TIMES", [], raising=False)
    monkeypatch.setattr(main, "_REFRESH_BURST_REPORTED_AT", 0.0, raising=False)
    assert _write_stable_session_script("c-nope-1") is False


def test_given_unchanged_script_when_writing_stable_script_then_it_reports_success(monkeypatch, tmp_path):
    """``ai internal write-stable-script`` exits 0 when the script is already current."""
    state = tmp_path / "ai-cli-utils"
    monkeypatch.setattr("ai_cli.config.get_xdg_state_home", lambda: state)
    monkeypatch.setattr(main, "_REFRESH_CALL_TIMES", [], raising=False)
    monkeypatch.setattr(main, "_REFRESH_BURST_REPORTED_AT", 0.0, raising=False)
    _install_session_meta(state, "c-sw-1", "sw-1")

    assert _write_stable_session_script("c-sw-1") is True
    assert _write_stable_session_script("c-sw-1") is True
