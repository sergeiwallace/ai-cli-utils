"""Shell helpers shared by the persistent supervisor and its replaceable child."""

import json
import shlex
from pathlib import Path

from . import config
from . import session_registry as registry
from . import session_run_state as state
from .session_lifecycle import persistence_settings, pinned_python

SESSION_STATE_FUNCTIONS = r"""
    # BEGIN RUN STATE helpers
    _ai_lifecycle() {
      local _ai_lifecycle_pid="" _ai_lifecycle_signal="" _ai_lifecycle_result=1
      local _ai_lifecycle_traps=""
      if [[ -n "${ZSH_VERSION:-}" ]]; then
        # zsh clears traps in command substitutions; restore them at function return.
        setopt localoptions localtraps no_bg_nice
      else
        _ai_lifecycle_traps="$(trap -p INT TERM)"
      fi
      trap '_ai_lifecycle_signal=INT; [[ -z "$_ai_lifecycle_pid" ]] || kill -INT "$_ai_lifecycle_pid" 2>/dev/null || true' INT
      trap '_ai_lifecycle_signal=TERM; [[ -z "$_ai_lifecycle_pid" ]] || kill -TERM "$_ai_lifecycle_pid" 2>/dev/null || true' TERM
      __LIFECYCLE_HELPER__ __LIFECYCLE_CONTEXT__ "$tmux_session" "$AI_CLI_RUN_GENERATION" "$@" &
      _ai_lifecycle_pid=$!
      [[ -z "$_ai_lifecycle_signal" ]] || kill -"$_ai_lifecycle_signal" "$_ai_lifecycle_pid" 2>/dev/null || true
      while true; do
        wait "$_ai_lifecycle_pid"
        _ai_lifecycle_result=$?
        kill -0 "$_ai_lifecycle_pid" 2>/dev/null || break
      done
      if [[ -z "${ZSH_VERSION:-}" ]]; then
        trap - INT TERM
        eval "$_ai_lifecycle_traps"
      fi
      return "$_ai_lifecycle_result"
    }
    _ai_mark_running() {
      _ai_lifecycle running unknown '{}'
    }
    _ai_persist_exit() {
      local _ai_exit_result=0
      _ai_lifecycle exited "$1" "$2" || _ai_exit_result=$?
      printf '%s\n' "$1" > "$_ai_state_dir/session-final-exit-$tmux_session" || true
      if (( _ai_exit_result != 0 )); then
        printf '%s\n' '[session] exit: Session exit persistence failed; the agent will remain stopped.' >&2
        exit 77
      fi
    }
    _ai_final_manual_exit() {
      if [[ ! -f "$_ai_state_dir/session-final-exit-$tmux_session" ]]; then
        _ai_persist_exit manual_exit '{}'
      fi
    }
    # END RUN STATE
"""


def lifecycle_command(path: Path) -> list[str]:
    """Pin both the installation and registry before project exports can redirect them."""
    return pinned_python(
        f"from pathlib import Path; from ai_cli import session_registry as registry; "
        f"registry.registry_path = lambda: Path({path.as_posix()!r}); from ai_cli.main import cli; cli()"
    )


def session_state_functions(name: str) -> str:
    cfg = config.load_config(read_only=True)
    doc = registry.load_registry()
    record = next((r for r in doc["sessions"] if r.get("kind") == "local" and r.get("name") == name), {})
    host = record.get("owning_host") or cfg.get("machine", {}).get("host_id") or doc["machine"]
    context = {
        "command": lifecycle_command(registry.registry_path()),
        "registry_path": registry.registry_path().as_posix(),
        "safety_path": state.tombstone_path(name, host).as_posix(),
        "policy": persistence_settings(cfg),
    }
    helper = pinned_python("from ai_cli.session_lifecycle import main; main()")
    return SESSION_STATE_FUNCTIONS.replace("__LIFECYCLE_HELPER__", shlex.join(helper)).replace(
        "__LIFECYCLE_CONTEXT__", shlex.quote(json.dumps(context))
    )
