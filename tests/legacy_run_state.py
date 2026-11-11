"""Run-state shell helpers for harnesses that stand in for `ai` on PATH.

The shipped supervisor persists run state through its own pinned installation, so a harness that
fakes `ai` cannot observe or answer those calls. Harnesses that test other supervisor behaviour
(reaping, suspend, reload, recovery) import `legacy_run_state_helpers` to get the earlier helpers,
which call the stand-in `ai` instead. The pinned path itself is covered against the real command
line in `test_session_exit_persistence.py` and `test_session_lifecycle_script.py`.
"""

import contextlib

import pytest

LEGACY_SESSION_STATE_FUNCTIONS = r"""
    # BEGIN RUN STATE helpers
    _ai_mark_running() {
      _run_sequence=$(ai sessions _sequence) || return 1
      ai sessions mark "$tmux_session" running \
        --generation "$AI_CLI_RUN_GENERATION" --sequence "$_run_sequence"
    }
    _ai_persist_exit() {
      while true; do
        _exit_sequence=$(ai sessions _sequence)
        if [[ -n "$_exit_sequence" ]] && ai sessions mark "$tmux_session" exited --cause "$1" --evidence "$2" \
          --generation "$AI_CLI_RUN_GENERATION" --sequence "$_exit_sequence"; then
          printf '%s\n' "$1" > "$_ai_state_dir/session-final-exit-$tmux_session"
          return 0
        fi
        _ai_emit info persistence "Session exit persistence failed; retrying. The agent will remain stopped."
        sleep 1
      done
    }
    _ai_final_manual_exit() {
      if [[ ! -f "$_ai_state_dir/session-final-exit-$tmux_session" ]]; then
        _ai_persist_exit manual_exit '{}'
      fi
    }
    # END RUN STATE
"""


@pytest.fixture(autouse=True)
def legacy_run_state_helpers(monkeypatch):
    import ai_cli.session_script as scripts

    monkeypatch.setattr(scripts, "session_state_functions", lambda _name: LEGACY_SESSION_STATE_FUNCTIONS)


@contextlib.contextmanager
def legacy_state_functions():
    """Generate scripts with the earlier helpers where no fixture is in scope (a nested pytest run)."""
    import ai_cli.session_script as scripts

    saved = scripts.session_state_functions
    scripts.session_state_functions = lambda _name: LEGACY_SESSION_STATE_FUNCTIONS
    try:
        yield
    finally:
        scripts.session_state_functions = saved
