"""Engine launch bash template generator.

Depends on: nothing (self-contained).
"""

import json
import re
import shutil
from pathlib import Path

# Interpreters that can run the generated session template, most preferred first.
# zsh stays first because that is what every host has used to date; the ordering,
# not the presence of zsh, is what this list encodes.
SESSION_SHELL_PREFERENCE = ("zsh", "bash")


def resolve_session_shell() -> str | None:
    """Absolute path of the shell that should interpret the session script.

    zsh remains the *preferred* interpreter wherever it is installed — switching
    hosts that have it would be a behavioural change, not a portability fix. bash
    is the fallback, and a safe one: the generated template uses only ``[[ ]]``,
    ``(( ))`` and POSIX builtins, and its own self-update branch already execs a
    refreshed template under ``bash``.

    Returns ``None`` when neither shell is installed, so the caller can fail with
    an actionable message. Handing tmux an interpreter that does not exist is the
    worst option available: ``tmux new-session`` still reports success, then the
    pane's exec fails, the pane dies, the session is torn down, and the user sees
    only a bare ``[exited]`` with no diagnostic.
    """
    for candidate in SESSION_SHELL_PREFERENCE:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _current_update_commit() -> str:
    """Return the source HEAD recorded by the last ``ai update`` (or "").

    ``ai update`` writes the just-installed git HEAD to
    ``~/.local/state/ai-cli-utils/last_update_commit.txt``. Baking this into the
    generated template lets the running wrapper detect an update by comparing the
    baked commit against the live stamp — a monotonic signal (git HEAD always
    advances), unlike the package version which ``ai update`` restores to its base
    value after install and can therefore read identical across updates.
    """
    try:
        stamp = Path.home() / ".local" / "state" / "ai-cli-utils" / "last_update_commit.txt"
        with stamp.open() as fh:
            return fh.read().strip()
    except Exception:
        return ""


def get_engine_script(
    engine: str,
    ai_name: str,
    session: str,
    prefix: str,
    project_prefix: str,
    session_id_uuid: str | None = None,
    sandbox: bool = False,
    worktree_dir: str | None = None,
    notify: bool = False,
    is_remote: bool = False,
    project_name: str = "",
    iterm2_slot: str | None = None,
    iterm2_cfg: dict | None = None,
    config_reload_idle_secs: int = 90,
    gemini_cmd: str = "gemini",
) -> str:
    # Validate UUID before interpolating into bash script (defense-in-depth)
    if session_id_uuid and not re.fullmatch(r"[0-9a-f-]{36}", session_id_uuid):
        session_id_uuid = ""
    env_var_prefix = {"c": "CC", "g": "GG", "p": "PI", "cx": "CX"}[engine]
    sandbox_flag = "-s" if sandbox else "--no-sandbox"
    cd_cmd = f"cd {worktree_dir}" if worktree_dir else ":"
    notify_cmd = 'ai internal notify "$tmux_session" "Agent Finished Task" 2>/dev/null || true' if notify else "true"
    try:
        from importlib.metadata import version as _pkg_version

        _template_version = _pkg_version("ai-cli-utils")
    except Exception:
        _template_version = "unknown"
    _template_commit = _current_update_commit()
    # Same interpreter the launcher hands tmux, baked in so a hot-reload/self-update
    # exec cannot resurrect a hard zsh dependency the launch path just resolved away.
    # ``or "bash"`` only guards template *generation* on a host with neither shell;
    # the launch path itself refuses to start in that case, with a real message.
    _session_shell = resolve_session_shell() or "bash"

    # Resolve iTerm2 slot values for embedding in bash template
    _cfg = iterm2_cfg or {}
    _it2_color = (iterm2_slot.lstrip("#") if iterm2_slot else None) or "e74c3c"
    _it2_show_type = "1" if _cfg.get("iterm2", {}).get("tab_title", {}).get("show_type_symbol", True) else "0"
    _it2_show_status = "1" if _cfg.get("iterm2", {}).get("tab_title", {}).get("show_status_symbol", True) else "0"

    # Metadata baked into the template so `ai internal refresh-template` can regenerate it.
    _meta = json.dumps(
        {
            "engine": engine,
            "ai_name": ai_name,
            "session": session,
            "prefix": prefix,
            "project_prefix": project_prefix,
            "session_id_uuid": session_id_uuid or "",
            "sandbox": sandbox,
            "worktree_dir": worktree_dir or "",
            "notify": notify,
            "is_remote": is_remote,
            "project_name": project_name,
            "iterm2_slot": iterm2_slot or "",
            "iterm2_cfg": iterm2_cfg or {},
            "config_reload_idle_secs": config_reload_idle_secs,
            "gemini_cmd": gemini_cmd,
        }
    )

    return f"""
    # The pane leader is deliberately stable.  Replaceable session bodies run as
    # children so a hot reload can never transfer the generation lease to a new
    # pane PID.
    _ai_cli_child_mode=false
    if [[ "${{1:-}}" == "--ai-cli-heartbeat-ticker" ]]; then
      shift
      tmux_session="$1"
      generation_token="$2"
      supervisor_pid="$3"
      while true; do
        heartbeat_json=$(printf '{{"status": "WORKING", "project": "%s", "ai_name": "%s"}}' "{project_prefix}" "{ai_name}")
        ai internal publish-heartbeat "$tmux_session" "$heartbeat_json" "$generation_token" "$supervisor_pid" 2>/dev/null || true
        sleep 30 || exit 0
      done
    fi
    if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
      _ai_cli_child_mode=true
      shift
    fi
    if ! $_ai_cli_child_mode; then
      tmux_session="{session}"
      ai_name="{ai_name}"
      engine="{engine}"
      _ai_state_dir="${{XDG_STATE_HOME:-$HOME/.local/state}}/ai-cli-utils"
      mkdir -p "$_ai_state_dir/session-leases" "$_ai_state_dir/session-heartbeats"
      generation_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null || true)
      _reaper_evidence_enabled=false
      _reaper_lease_fd=""
      _supervisor_signal_model_reason=""
      _verify_supervisor_signal_model() {{
        # The supervisor must begin in the pane foreground group so it can
        # promote each separately grouped child to the terminal foreground.
        if ! set +m 2>/dev/null; then
          _supervisor_signal_model_reason="could not disable shell job control"
          return 1
        fi
        if [[ -o monitor ]]; then
          _supervisor_signal_model_reason="shell job control remains enabled"
          return 1
        fi
        if ! _supervisor_pgid=$(ps -o pgid= -p "$$" 2>/dev/null); then
          _supervisor_signal_model_reason="could not read supervisor process group"
          return 1
        fi
        if ! _terminal_pgid=$(ps -o tpgid= -p "$$" 2>/dev/null); then
          _supervisor_signal_model_reason="could not read terminal foreground process group"
          return 1
        fi
        _supervisor_pgid="${{_supervisor_pgid//[[:space:]]/}}"
        _terminal_pgid="${{_terminal_pgid//[[:space:]]/}}"
        if [[ ! "$_supervisor_pgid" =~ ^[0-9]+$ ]] || [[ ! "$_terminal_pgid" =~ ^[1-9][0-9]*$ ]]; then
          _supervisor_signal_model_reason="process-group probe returned an unusable value"
          return 1
        fi
        if [[ "$_supervisor_pgid" != "$_terminal_pgid" ]]; then
          _supervisor_signal_model_reason="supervisor is not in the terminal foreground process group"
          return 1
        fi
        return 0
      }}
      if _verify_supervisor_signal_model; then
        _supervisor_signal_model_verified=true
      else
        _supervisor_signal_model_verified=false
        printf '%s\\n' "ai-cli: stale-session reaper evidence disabled: $_supervisor_signal_model_reason" >&2
      fi
      if $_supervisor_signal_model_verified && [[ -n "$generation_token" ]] && tmux set-option -t "$tmux_session" @ai_cli_session_generation "$generation_token" 2>/dev/null; then
        _lease_session=$(printf '%s' "$tmux_session" | base64 | tr '/+' '_-' | tr -d '=\\n')
        _lease_generation=$(printf '%s' "$generation_token" | base64 | tr '/+' '_-' | tr -d '=\\n')
        _lease_path="$_ai_state_dir/session-leases/${{_lease_session}}-${{_lease_generation}}.lock"
        exec {{_reaper_lease_fd}}>"$_lease_path"
        if ai internal acquire-generation-lease "$_reaper_lease_fd"; then
          _reaper_evidence_enabled=true
          export AI_CLI_SUPERVISOR_LEASE_FD="$_reaper_lease_fd"
        else
          _supervisor_signal_model_reason="generation lease backend unavailable"
          printf '%s\n' "ai-cli: stale-session reaper evidence disabled: $_supervisor_signal_model_reason" >&2
        fi
      fi
      _heartbeat_pid=""
      _supervisor_cleanup() {{
        [[ -n "$_heartbeat_pid" ]] && kill "$_heartbeat_pid" 2>/dev/null || true
        ai internal revoke-heartbeat "$tmux_session" "$generation_token" 2>/dev/null || true
        rm -f "$_ai_state_dir/session-meta-$tmux_session.json" \\
          "$_ai_state_dir/config-hash-$tmux_session" "$_ai_state_dir/config-changed-$tmux_session"
        ai internal cleanup-worktree "$ai_name" 2>/dev/null
        ai internal release-color-slot "$ai_name" 2>/dev/null
        ai internal cleanup-session-files "$ai_name" 2>/dev/null
      }}
      _supervisor_wait_for_child() {{
        # `wait` is interrupted by a trapped signal in Bash and zsh. Keep
        # waiting while the child is live so record-only supervisor signals do
        # not start a second child or leave the first child behind.
        _child_wait_status=0
        while kill -0 "$_child_pid" 2>/dev/null; do
          wait "$_child_pid"
          _child_wait_status=$?
        done
        return "$_child_wait_status"
      }}
      _supervisor_promote_child() {{
        # A terminal-backed child becomes its own process group in the exec
        # wrapper. Promote that group before it can read terminal input.
        [[ -t 0 ]] || return 0
        trap '' TTOU
        _promotion_attempt=0
        while (( _promotion_attempt < 50 )); do
          if python3 -c 'import os, signal, sys; pgid = int(sys.argv[1]); os.tcsetpgrp(0, pgid); os.killpg(pgid, signal.SIGCONT)' "$_child_pid" 2>/dev/null; then
            return 0
          fi
          sleep 0.01
          _promotion_attempt=$((_promotion_attempt + 1))
        done
        return 1
      }}
      _supervisor_restore_terminal() {{
        [[ -t 0 ]] || return 0
        python3 -c 'import os, sys; os.tcsetpgrp(0, int(sys.argv[1]))' "$_supervisor_pgid" 2>/dev/null
      }}
      _supervisor_term() {{
        if $_supervisor_terminating; then
          return
        fi
        _supervisor_terminating=true
        if [[ -n "${{_child_pid:-}}" ]] && kill -0 "$_child_pid" 2>/dev/null; then
          kill -TERM "$_child_pid" 2>/dev/null || true
          _supervisor_wait_for_child || true
        fi
        exit 0
      }}
      _supervisor_terminating=false
      _supervisor_int_count=0
      _supervisor_winch_count=0
      _supervisor_record_int() {{ _supervisor_int_count=$((_supervisor_int_count + 1)); }}
      _supervisor_record_winch() {{ _supervisor_winch_count=$((_supervisor_winch_count + 1)); }}
      trap '_supervisor_record_int' INT
      trap '_supervisor_record_winch' WINCH
      trap '_supervisor_term' TERM
      trap '_supervisor_cleanup' EXIT
      _supervisor_script="$0"
      _supervisor_terminal_fd=""
      if [[ -t 0 ]]; then
        # zsh redirects a background command's fd 0 before its exec wrapper
        # runs. Preserve the pane terminal on a second descriptor for that
        # wrapper to restore after it has made the child a new process group.
        exec 9<&0
        _supervisor_terminal_fd=9
        export AI_CLI_SUPERVISOR_TERMINAL_FD="$_supervisor_terminal_fd"
      fi
      if $_reaper_evidence_enabled; then
        # A terminal-free companion owns only its timer. It starts a new session
        # before execing the ticker so foreground-group changes cannot affect it.
        python3 -c 'import os, sys; fd = os.environ.get("AI_CLI_SUPERVISOR_LEASE_FD"); fd and os.close(int(fd)); os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
          "{_session_shell}" "$_supervisor_script" --ai-cli-heartbeat-ticker "$tmux_session" "$generation_token" "$$" \
          </dev/null >/dev/null 2>&1 &
        _heartbeat_pid=$!
        disown "$_heartbeat_pid" 2>/dev/null || true
      fi
      while true; do
        if [[ -f "$_ai_state_dir/sessions/$tmux_session.sh" ]]; then
          _supervisor_script="$_ai_state_dir/sessions/$tmux_session.sh"
        fi
        # With job control disabled, Bash backgrounds a command with SIGINT and
        # SIGQUIT ignored and redirects stdin unless it is explicit. Reset the
        # dispositions in a short exec wrapper before the child shell starts,
        # retain stdin explicitly, and then wait interruptibly in this shell.
        python3 -c 'import os, signal, sys; fd = os.environ.get("AI_CLI_SUPERVISOR_LEASE_FD"); fd and os.close(int(fd)); terminal_fd = os.environ.get("AI_CLI_SUPERVISOR_TERMINAL_FD"); terminal_fd and os.dup2(int(terminal_fd), 0); os.isatty(0) and (os.setpgrp() or os.kill(os.getpid(), signal.SIGSTOP)); signal.signal(signal.SIGINT, signal.SIG_DFL); signal.signal(signal.SIGQUIT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' \
          "$_supervisor_script" --ai-cli-child-body <&0 &
        _child_pid=$!
        if ! _supervisor_promote_child; then
          printf '%s\n' "ai-cli: could not promote child process group to terminal foreground" >&2
          kill -TERM "$_child_pid" 2>/dev/null || true
          _supervisor_wait_for_child || true
          exit 1
        fi
        _supervisor_wait_for_child
        _child_status=$?
        _supervisor_restore_terminal || true
        # 78 requests a refreshed child after a stable-script/self-update check;
        # 77 is clean local final exit and 79 is remote recovery-shell completion.
        if (( _child_status == 77 || _child_status == 79 )); then
          break
        fi
      done
      exit 0
    fi
    {cd_cmd}
    direnv_root="$PWD"
    agent_direnv_blocked=false
    agent_used_direnv=false
    active_agent_pid=""
    _child_term() {{
      # The supervisor relays only a supervisor-directed SIGTERM to this child.
      # The active agent shares the terminal process group, but receives this
      # explicit copy as well so child shutdown cannot orphan it.
      if [[ -n "$active_agent_pid" ]] && kill -0 "$active_agent_pid" 2>/dev/null; then
        kill -TERM "$active_agent_pid" 2>/dev/null || true
        wait "$active_agent_pid" 2>/dev/null || true
      fi
      exit 143
    }}
    trap '_child_term' TERM
    run_agent() {{
      # direnv is an enhancement, never a precondition for starting a session —
      # the same invariant the bare launch path (`_exec_with_direnv`) documents and
      # enforces. Unguarded, `direnv exec` on a host without direnv makes every
      # launch exit 127 before the agent runs, which the elapsed<3 guard below then
      # reports as an .envrc *approval* problem — pointing at a trust prompt when
      # the binary simply is not installed.
      if command -v direnv >/dev/null 2>&1; then
        agent_used_direnv=true
        direnv exec "$direnv_root" "$@" &
      else
        agent_used_direnv=false
        "$@" &
      fi
      active_agent_pid=$!
      wait "$active_agent_pid"
      agent_exit_code=$?
      active_agent_pid=""
      if (( agent_exit_code != 0 )) && $agent_used_direnv; then
        # A cheap, isolated re-probe (not a redirect on the command above) — that
        # command is a long-running interactive process (claude/gemini), and
        # capturing its stderr for later inspection would buffer it instead of
        # streaming to the terminal in real time. direnv's block state is
        # deterministic given .envrc + approval state, so this probe reflects
        # the same outcome the failed launch just hit, without touching its I/O.
        if direnv exec "$direnv_root" true 2>&1 | grep -qE 'direnv: error.*[.]envrc is blocked'; then
          agent_direnv_blocked=true
        fi
        echo "Error: agent command did not complete successfully under direnv for $direnv_root. If direnv denied or could not evaluate .envrc, run 'direnv allow $direnv_root' and correct the reported error." >&2
      fi
      return "$agent_exit_code"
    }}
    first_run=true
    ai_name="{ai_name}"
    engine="{engine}"
    tmux_session="{session}"
    # If this script was exec'd by hot-reload, AI_SESSION_STARTED is set in the tmux
    # env — skip first-run-only setup so CC relaunches cleanly without re-running
    # iTerm2 fleet wait, or session-broker on each auto-restart.
    if [[ "$(tmux show-environment -t "$tmux_session" AI_SESSION_STARTED 2>/dev/null)" == "AI_SESSION_STARTED=1" ]]; then
      first_run=false
    fi
    _template_version="{_template_version}"
    _template_commit="{_template_commit}"
    uuid="{session_id_uuid or ""}"
    project_prefix="{project_prefix}"
    project_name="{project_name}"
    _ai_state_dir="${{XDG_STATE_HOME:-$HOME/.local/state}}/ai-cli-utils"
    mkdir -p "$_ai_state_dir/iterm2" "$_ai_state_dir/sessions"
    # Stable script path — written by `ai c` on every launch/re-attach.
    # Mtime changes when a new version is installed and `ai c` re-attaches.
    _script_stable_path="$_ai_state_dir/sessions/$tmux_session.sh"
    _script_start_mtime=$(stat -f "%m" "$_script_stable_path" 2>/dev/null || stat -c "%Y" "$_script_stable_path" 2>/dev/null || echo "0")
    printf '%s' {json.dumps(_meta)} > "$_ai_state_dir/session-meta-$tmux_session.json"

    # iTerm2 slot assigned by Python at launch time (collision-free lease system).
    # These variables are constant for the lifetime of this session.
    # Profile name is deterministic: ai-cli:$ai_name (generated Dynamic Profile).
    _iterm2_color="{_it2_color}"
    _iterm2_show_type_sym="{_it2_show_type}"
    _iterm2_show_status_sym="{_it2_show_status}"

    # --dangerously-skip-permissions is blocked when running as root
    if [[ $(id -u) -eq 0 ]]; then
      claude_perms_flag=""
    else
      claude_perms_flag="--dangerously-skip-permissions"
    fi

    if [[ "$engine" == "c" ]]; then
      signal_file="$_ai_state_dir/cc-exit-$tmux_session"
      prompt_file="$_ai_state_dir/cc-resume-prompt-$tmux_session"
    else
      signal_file="$_ai_state_dir/gg-exit-$tmux_session"
      reload_file="$_ai_state_dir/gg-reload-$tmux_session"
      restart_file="$_ai_state_dir/gg-restart-$tmux_session"
      prompt_file="$_ai_state_dir/gg-resume-prompt-$tmux_session"
    fi
    lock_file="$_ai_state_dir/ai-watcher-lock-$tmux_session"
    config_hash_file="$_ai_state_dir/config-hash-$tmux_session"
    config_changed_file="$_ai_state_dir/config-changed-$tmux_session"
    _config_reload_idle_secs={config_reload_idle_secs}

    # Files whose CHANGE should trigger an idle-restart. CLAUDE.md content is
    # re-injected every turn (a genuinely live reload), but .claude/settings.json's
    # `env` block is read ONLY at CC process startup — a `/compact` never re-reads it,
    # so an override left there (e.g. CLAUDE_AUTOCOMPACT_PCT_OVERRIDE) silently stays
    # baked into an already-running session forever unless this watcher also tracks
    # settings.json (AI-CLI-115 — confirmed live: a session ran 2+ days past a
    # settings.json fix with the stale env still active, because only CLAUDE.md was
    # hashed here).
    _config_watch_files="$HOME/projects/CLAUDE.md $(pwd)/CLAUDE.md $HOME/.claude/settings.json $(pwd)/.claude/settings.json $(pwd)/.mcp.json"

    # Write initial config hash baseline for change detection
    cat $_config_watch_files 2>/dev/null | sha256sum | cut -d' ' -f1 > "$config_hash_file"

    # Clean up any stale exit signals from a previous killed session.
    # Without this, a leftover signal_file causes the watcher to inject /exit
    # while CC is still showing its startup UI on the very next launch.
    rm -f "$signal_file" "$config_changed_file"

    export AI_TMUX_SESSION="$tmux_session"
    export {env_var_prefix}_TMUX_SESSION="$tmux_session"
    watcher_pid=""

    start_watcher() {{
      if [[ -n "$watcher_pid" ]]; then
        kill "$watcher_pid" 2>/dev/null || true
        watcher_pid=""
      fi
      rm -f "$lock_file"

      (echo $$ > "$lock_file"
      trap 'rm -f "$lock_file"' EXIT
      counter=0
      while true; do
        (( counter++ ))

        if [[ -f "$signal_file" ]]; then
          # Only inject /exit when CC is at the idle empty prompt (❯ alone on the
          # last visible line). Four layers of protection against false positives:
          #
          # 1. Grace period (counter < 10): skip the first 10s after watcher start.
          #    When CC restarts with --continue, the pane still shows the previous
          #    conversation's ❯ for 1-3s while CC loads. Without this guard, the
          #    watcher fires injection into CC's startup TUI, causing the rewind menu.
          #    counter resets to 0 every time start_watcher is called (top of each
          #    while-loop iteration), which is always right before CC launches.
          #
          # 2. Double capture-pane: verify ❯ is stable across two back-to-back
          #    samples before acting. A transient ❯ during startup or state
          #    transition will fail the second check and be skipped.
          #
          # 3. /exit pane guard: before injecting, scan the full visible pane for
          #    '/exit' text. If present, a prior watcher subshell (SIGTERMed between
          #    send-keys and rm -f) already queued /exit — skip re-injection. CC will
          #    exit from the already-queued /exit. signal_file is still cleaned up.
          #
          # 4. signal_file deleted after double-verify (regardless of whether /exit
          #    was sent): rm and break are outside the /exit guard so they fire on
          #    both inject and skip paths. Prevents signal_file from persisting when
          #    the guard fires.
          #
          # C-u removed: the guard already confirms an empty prompt; C-u is
          # redundant and has unknown behavior in CC's React/Ink TUI.
          if (( counter >= 10 )); then
            _sig_last=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1)
            if echo "$_sig_last" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
              _sig_verify=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1)
              if echo "$_sig_verify" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
                if ! tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -qF '/exit'; then
                  if [[ "$engine" == "g" ]]; then
                    tmux send-keys -t "$tmux_session" "/resume save $ai_name" C-m
                    sleep 2
                  fi
                  tmux send-keys -t "$tmux_session" '/exit' C-m
                fi
                rm -f "$signal_file"
                break
              fi
            fi
          fi
          # CC not at idle prompt, or within startup grace period — keep signal_file, retry next cycle
        fi

        # Config change detection (CC only, every 10s)
        if [[ "$engine" == "c" ]] && (( counter % 10 == 0 )); then
          _current_hash=$(cat $_config_watch_files 2>/dev/null | sha256sum | cut -d' ' -f1)
          _last_hash=$(cat "$config_hash_file" 2>/dev/null || echo "")
          if [[ -n "$_current_hash" && "$_current_hash" != "$_last_hash" && ! -f "$config_changed_file" ]]; then
            date +%s > "$config_changed_file"
          fi
        fi

        # Auto-restart when config changed and session has been idle long enough.
        # Same grace period as signal_file path: skip first 10s to avoid acting
        # on stale pane content from before CC finished launching.
        if [[ -f "$config_changed_file" && ! -f "$signal_file" ]] && (( counter >= 10 )); then
          _changed_at=$(cat "$config_changed_file" 2>/dev/null || echo 0)
          _idle_secs=$(( $(date +%s) - _changed_at ))
          if (( _idle_secs >= _config_reload_idle_secs )); then
            _last_line=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1)
            if echo "$_last_line" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
              _new_hash=$(cat $_config_watch_files 2>/dev/null | sha256sum | cut -d' ' -f1)
              echo "$_new_hash" > "$config_hash_file"
              rm -f "$config_changed_file"
              touch "$signal_file"
            fi
          fi
        fi
        if [[ "$engine" == "g" && -f "$reload_file" ]]; then
          rm -f "$reload_file"
          tmux send-keys -t "$tmux_session" Escape
          sleep 0.2
          tmux send-keys -t "$tmux_session" C-u
          tmux send-keys -t "$tmux_session" "/memory reload" C-m
        fi
        if [[ "$engine" == "g" && -f "$restart_file" ]]; then
          rm -f "$restart_file"
          tmux send-keys -t "$tmux_session" Escape
          sleep 0.2
          tmux send-keys -t "$tmux_session" C-u
          tmux send-keys -t "$tmux_session" "R"
        fi
        # One tick per second — every duration in this loop is counted in ticks
        # ("counter >= 10" == 10s, "counter % 30" == every 30s). This must actually
        # block: `read -t 1 < /dev/null` does not, because /dev/null returns EOF
        # immediately, which turned this into a busy loop firing tmux/ai/sha256sum
        # subprocesses at ~140 Hz for the life of every session (AI-CLI-129).
        sleep 1
      done) &
      watcher_pid=$!
    }}

    # Auto-clean orphaned processes at session start (score >= 80, local only).
    # Runs in foreground so orphans are gone before CC launches. Suppressed
    # when process_hygiene.auto_clean is false in config.toml.
    ai ps cron &>/dev/null || true

    # Auto-start sync watch and memory watch (PID files prevent duplicates)
    ai sync watch &>/dev/null &
    ai memory watch &>/dev/null &

    # Auto-start quota-watch (idempotent — circusd skips if already registered).
    # Gated on [quota_watch] auto_start in config.toml (default off — see config.py).
    ai quota watch start --auto 2>/dev/null || true

    # iTerm2 fleet management: set profile, tab color, pane title.
    # Color slot was assigned by Python before tmux launched (collision-free lease).
    # _it2: wraps OSC sequences in DCS passthrough when inside tmux.
    _it2() {{
      if [[ -n "$TMUX" ]]; then
        printf '\\033Ptmux;\\033%b\\033\\\\' "$1"
      else
        printf '%b' "$1"
      fi
    }}

    # Rename the pane iTerm2 shows this tmux session in.  `set-iterm2-name`
    # resolves the target pane from the session's live client tty, so it always
    # hits the pane the user is currently viewing and can never collide with
    # another session (unlike the old shared-GUID scheme, AI-CLI-59).  Only fires
    # when the session is attached — a detached session has no client tty and
    # must not touch any pane.  Falls back to OSC 1 when not attached.
    _iterm2_rename() {{
      local dname="$1"
      if [[ $(tmux list-clients -t "$tmux_session" 2>/dev/null | wc -l) -gt 0 ]]; then
        ( ai internal set-iterm2-name "$tmux_session" "$dname" 2>/dev/null ) &
      else
        _it2 "\\033]1;$dname\\007"
      fi
    }}

    _iterm2_fleet_setup() {{
      [[ "$LC_TERMINAL" != "iTerm2" && "$TERM_PROGRAM" != "iTerm.app" ]] && return 0
      local sname="$1"
      # Profile name is deterministic — matches the Dynamic Profile generated by Python
      local _profile="ai-cli:$ai_name"
      _it2 "\\033]1337;SetProfile=$_profile\\007"
      _it2 "\\033]1337;SetColors=tab=$_iterm2_color\\007"
      _iterm2_rename "$sname"
    }}

    # iTerm2 status updates: re-emit pane title with optional status symbol.
    _iterm2_status() {{
      [[ "$LC_TERMINAL" != "iTerm2" && "$TERM_PROGRAM" != "iTerm.app" ]] && return 0
      local _st="$1" stype="$2" sname="$3"
      local type_sym="" sym=""
      [[ "$_iterm2_show_type_sym" == "1" ]] && {{
        [[ "$stype" == "cc" ]]     && type_sym="* "
        [[ "$stype" == "gemini" ]] && type_sym="✦ "
        [[ "$stype" == "pi" ]]     && type_sym="π "
      }}
      if [[ "$_iterm2_show_status_sym" == "1" ]]; then
        sym="▶"
        case "$_st" in
          done)     sym="✓" ;;
          error)    sym="✗" ;;
          resuming) sym="↻" ;;
          waiting)  sym="⏸" ;;
        esac
        sym="$sym "
      fi
      _iterm2_rename "${{type_sym}}${{sym}}$sname"
    }}

    # Extract session number from ai_name (e.g., "sw-3" → "3") for downstream hooks.
    _session_num=$(echo "$ai_name" | grep -oE '[0-9]+$' || echo "1")
    _session_type="cc"
    [[ "$engine" == "g" ]] && _session_type="gemini"
    [[ "$engine" == "p" ]] && _session_type="pi"

    # Export for CC Notification hook to use
    export ITERM2_SESSION_NUM="$_session_num"
    export ITERM2_SESSION_TYPE="$_session_type"

    # Stable task namespace: pin the CC task list to the session name so it survives
    # process restarts and context-file reloads without orphaning open tasks.
    # CC creates ~/.claude/tasks/$ai_name/ as a permanent, human-readable namespace.
    # (anthropics/claude-code#20664 — CLAUDE_CODE_TASK_LIST_ID env var override)
    [[ "$engine" == "c" ]] && export CLAUDE_CODE_TASK_LIST_ID="$ai_name"

    # Only the persistent supervisor owns final cleanup.  A child may be replaced
    # many times, so its EXIT trap can stop only its per-child monitor.
    trap 'kill "$watcher_pid" 2>/dev/null; rm -f "$lock_file"' EXIT

    while true; do
      # Hot-reload: if `ai c` wrote a fresh script to the stable path (e.g. after
      # `ai update`), exec it now so new template takes effect on this CC restart.
      # Runs at loop top so the running CC process is undisturbed; takes effect on
      # the restart after CC exits.  AI_SESSION_STARTED guard in the new script
      # ensures first_run=false so no duplicate setup runs.
      if [[ -f "$_script_stable_path" ]]; then
        _cur_mtime=$(stat -f "%m" "$_script_stable_path" 2>/dev/null || stat -c "%Y" "$_script_stable_path" 2>/dev/null || echo "0")
        if [[ "$_cur_mtime" != "$_script_start_mtime" && "$_cur_mtime" != "0" ]]; then
          echo "ai-cli session script updated — reloading..."
          exit 78
        fi
      fi
      start_watcher
      start_ts=$(date +%s)
      # Re-emit iTerm2 setup + set status to running.
      # On first launch, wait for the tmux client to attach — DCS passthrough sequences
      # are discarded when no client is connected, so firing before attach is a no-op.
      if $first_run && [[ -n "$TMUX" ]]; then
        _cli_wait=0
        while [[ $( tmux list-clients -t "$tmux_session" 2>/dev/null | wc -l ) -eq 0 ]] && (( _cli_wait < 20 )); do
          sleep 0.05
          (( _cli_wait++ ))
        done
      fi
      _iterm2_fleet_setup "$tmux_session"
      _iterm2_status "running" "$_session_type" "$tmux_session"

      (ai internal publish-event "$tmux_session" "START" 2>/dev/null || true) &
      (ai internal publish-session-event "$tmux_session" "started" 2>/dev/null || true) &

      if [[ -f "scripts/session-broker.py" ]] && $first_run; then
        # Run async so CC launches immediately. Context file written in background;
        # available by the time the first real prompt is processed.
        timeout 20 python3 scripts/session-broker.py --engine "$engine" &>/dev/null &
      fi

      # Pre-launch settings override. Some Claude Code feature checks resolve
      # once, very early in process startup, before a same-process settings
      # change (e.g. one written by a SessionStart hook) can influence them.
      # Writing the override here, before the agent process starts at all,
      # sidesteps that ordering entirely instead of racing it. Runs on every
      # launch and every restart, not just first run, since it sits
      # ahead of every run_agent invocation below.
      if [[ "$engine" == "c" ]]; then
        python3 -c "
import json, os
path = '.claude/settings.local.json'
data = {{}}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {{}}
data.setdefault('env', {{}})['DISABLE_GROWTHBOOK'] = ''
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true
      fi

      # Resolve before every Claude Code launch so its exact transcript UUID can
      # be passed to --resume.
      if [[ "$engine" == "c" ]]; then
        matched_file=$(ai internal resolve-continue-target "$PWD" "$ai_name")
        resolve_status=$?
      fi

      if [[ -f "$prompt_file" ]]; then
        resume_msg=$(cat "$prompt_file")
        rm -f "$prompt_file"
        if [[ "$engine" == "c" ]]; then
          if [[ $resolve_status -eq 0 && -n "$matched_file" ]]; then
            session_id="${{matched_file##*/}}"
            session_id="${{session_id%.jsonl}}"
            run_agent claude $claude_perms_flag --resume "$session_id" --name "$ai_name" "$resume_msg"
          else
            run_agent claude $claude_perms_flag --name "$ai_name" "$resume_msg"
          fi
        elif [[ "$engine" == "g" ]]; then
          (sleep 4; tmux send-keys -t "$tmux_session" "$resume_msg" C-m) &
          if [[ -n "$uuid" ]]; then run_agent {gemini_cmd} -y {sandbox_flag} -r "$uuid"
          else run_agent {gemini_cmd} -y {sandbox_flag} -i "/resume load $ai_name"
          fi
        elif [[ "$engine" == "p" ]]; then
          (sleep 4; tmux send-keys -t "$tmux_session" "$resume_msg" C-m) &
          if $first_run; then run_agent pi --name "$ai_name"
          else run_agent pi --continue --name "$ai_name"
          fi
        else
          (sleep 4; tmux send-keys -t "$tmux_session" "$resume_msg" C-m) &
          if $first_run; then run_agent codex
          else run_agent codex resume --last
          fi
        fi
      else
        if [[ "$engine" == "c" ]]; then
          # Find the most recent conversation matching $ai_name by customTitle.
          # The shared resolver checks the session registry before returning its
          # transcript UUID. A live transcript cannot be resumed safely.
          if [[ $resolve_status -eq 0 && -n "$matched_file" ]]; then
            session_id="${{matched_file##*/}}"
            session_id="${{session_id%.jsonl}}"
            run_agent claude $claude_perms_flag --resume "$session_id" --name "$ai_name"
          else
            run_agent claude $claude_perms_flag --name "$ai_name"
          fi
        elif [[ "$engine" == "g" ]]; then
          if [[ -n "$uuid" ]]; then run_agent {gemini_cmd} -y {sandbox_flag} -r "$uuid"
          else run_agent {gemini_cmd} -y {sandbox_flag} -i "/resume load $ai_name"
          fi
        elif [[ "$engine" == "p" ]]; then
          if $first_run; then run_agent pi --name "$ai_name"
          else run_agent pi --continue --name "$ai_name"
          fi
        else
          if $first_run; then run_agent codex
          else run_agent codex resume --last
          fi
        fi
      fi

      # Set iTerm2 status based on how CC exited + publish NATS event for gateway
      _exit_elapsed=$(( $(date +%s) - start_ts ))
      if (( _exit_elapsed < 3 )); then
        _iterm2_status "error" "$_session_type" "$tmux_session"
        (ai internal publish-session-event "$tmux_session" "error" 2>/dev/null || true) &
      else
        _iterm2_status "done" "$_session_type" "$tmux_session"
        (ai internal publish-session-event "$tmux_session" "completed" 2>/dev/null || true) &
      fi

      {notify_cmd}

      if [[ "$engine" == "g" ]]; then
        new_uuid=$(ai internal get-latest-gemini-id "$ai_name" 2>/dev/null)
        if [[ -n "$new_uuid" ]]; then
          uuid="$new_uuid"
          ai internal update-session-map g "$ai_name" "$uuid" 2>/dev/null
        fi
      fi

      first_run=false
      # Mark session as started so hot-reload exec skips first-run-only setup blocks.
      tmux set-environment -t "$tmux_session" AI_SESSION_STARTED 1 2>/dev/null || true
      elapsed=$_exit_elapsed
      if (( elapsed < 3 )); then
        if $agent_direnv_blocked; then
          echo "AI CLI stopped because direnv blocked $direnv_root/.envrc. Run 'direnv allow $direnv_root' in another terminal, then run 'ai $engine $ai_name' to relaunch."
        fi
        echo "AI CLI exited too quickly ($elapsed s) — stopping. Run 'ai c' to retry."
        break
      fi
      _iterm2_status "resuming" "$_session_type" "$tmux_session"
      # Self-update: if ai-cli was reinstalled/updated, exec a fresh template so new
      # changes take effect on this restart. exec replaces only this bash process
      # inside the tmux window; mosh connects to the tmux session (not this PID).
      #
      # Trigger primarily on the update-commit stamp (monotonic — `ai update` writes a
      # fresh git HEAD every time), with the package version as a secondary signal. The
      # old version-only trigger was unreliable: `ai update` restores pyproject to the
      # base version after install, so importlib metadata can read identical across
      # updates and the self-update would silently never fire.
      _current_ver=$(ai internal get-version 2>/dev/null || echo "unknown")
      _current_commit=$(cat "$_ai_state_dir/last_update_commit.txt" 2>/dev/null || echo "")
      _need_reload=false
      if [[ -n "$_current_commit" && "$_current_commit" != "$_template_commit" ]]; then
        _need_reload=true
      elif [[ "$_current_ver" != "unknown" && "$_current_ver" != "$_template_version" ]]; then
        _need_reload=true
      fi
      if $_need_reload; then
        echo "ai-cli updated — reloading session template..."
        _refresh_script=$(ai internal refresh-template "$tmux_session" 2>/dev/null)
        if [[ -n "$_refresh_script" && -f "$_refresh_script" ]]; then
          exit 78
        elif [[ -f "$_script_stable_path" ]]; then
          # Fall back to the stable script `ai update` rewrote for this session.
          exit 78
        else
          # Do NOT advance _template_version/_template_commit on failure — a transient
          # refresh error must not permanently disable self-update. Retry next restart.
          echo "Template refresh failed — will retry on next restart (or run 'ai c $ai_name')."
        fi
      fi
      # The persistent supervisor, not this replaceable child, owns restarts.
      # Returning resets child-local monitor state before the next agent launch.
      exit 0
    done
    (ai internal publish-event "$tmux_session" "STOP" 2>/dev/null || true) &
    (ai internal publish-session-event "$tmux_session" "stopped" 2>/dev/null || true) &
    {('echo "Session ended. Exit shell to close tmux session."; "$SHELL"; exit 79') if is_remote else "exit 77"}
    """
