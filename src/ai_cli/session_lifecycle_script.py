"""Shell helpers shared by the persistent supervisor and its replaceable child."""

SESSION_STATE_FUNCTIONS = r"""
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
