"""Shell helpers shared by the persistent supervisor and its replaceable child."""

SESSION_STATE_FUNCTIONS = r"""
    _ai_mark_running() {
      _run_sequence=$(python3 -c 'import time; print(time.monotonic_ns())') || return 1
      python3 -c '
import subprocess, sys
try:
    result = subprocess.run(["ai", "sessions", "mark", sys.argv[1], "running",
                             "--generation", sys.argv[2], "--sequence", sys.argv[3]],
                            check=False, timeout=5)
    sys.exit(result.returncode)
except (OSError, subprocess.TimeoutExpired):
    print("Session startup persistence failed; refusing to start the agent", file=sys.stderr)
    sys.exit(1)
' "$tmux_session" "$AI_CLI_RUN_GENERATION" "$_run_sequence"
    }
    _ai_persist_exit() {
      while true; do
        _exit_sequence=$(python3 -c 'import time; print(time.monotonic_ns())')
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
"""
