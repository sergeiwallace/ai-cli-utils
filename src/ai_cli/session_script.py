"""Engine launch bash template generator.

Depends on: nothing (self-contained).
"""

import json
import re


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
    env_var_prefix = "CC" if engine == "c" else "GG"
    sandbox_flag = "-s" if sandbox else "--no-sandbox"
    cd_cmd = f"cd {worktree_dir}" if worktree_dir else ":"
    notify_cmd = 'ai internal notify "$tmux_session" "Agent Finished Task" 2>/dev/null || true' if notify else "true"
    try:
        from importlib.metadata import version as _pkg_version

        _template_version = _pkg_version("ai-cli-utils")
    except Exception:
        _template_version = "unknown"

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

    script = f"""
    {cd_cmd}
    first_run=true
    ai_name="{ai_name}"
    engine="{engine}"
    tmux_session="{session}"
    # If this script was exec'd by hot-reload, AI_SESSION_STARTED is set in the tmux
    # env — skip first-run-only setup so CC relaunches cleanly without re-running
    # handoff drain, iTerm2 fleet wait, or session-broker on each auto-restart.
    if [[ "$(tmux show-environment -t "$tmux_session" AI_SESSION_STARTED 2>/dev/null)" == "AI_SESSION_STARTED=1" ]]; then
      first_run=false
    fi
    _template_version="{_template_version}"
    uuid="{session_id_uuid or ""}"
    project_prefix="{project_prefix}"
    project_name="{project_name}"
    _ai_state_dir="$HOME/.local/state/ai-cli-utils"
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
    handoff_pending_file="$_ai_state_dir/handoff-pending-$tmux_session"
    config_hash_file="$_ai_state_dir/config-hash-$tmux_session"
    config_changed_file="$_ai_state_dir/config-changed-$tmux_session"
    _config_reload_idle_secs={config_reload_idle_secs}

    # Write initial config hash baseline for change detection
    cat "$HOME/projects/CLAUDE.md" "$(pwd)/CLAUDE.md" 2>/dev/null | sha256sum | cut -d' ' -f1 > "$config_hash_file"

    # Clean up any stale exit signals from a previous killed session.
    # Without this, a leftover signal_file causes the watcher to inject /exit
    # while CC is still showing its startup UI on the very next launch.
    rm -f "$signal_file" "$config_changed_file"

    export AI_TMUX_SESSION="$tmux_session"
    export {env_var_prefix}_TMUX_SESSION="$tmux_session"
    watcher_pid=""
    signal_watch_pid=""

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
        if (( counter % 30 == 0 )); then
          heartbeat_json=$(printf '{{"status": "WORKING", "project": "%s", "ai_name": "%s"}}' "$project_prefix" "$ai_name")
          ai internal publish-heartbeat "$tmux_session" "$heartbeat_json" 2>/dev/null || true
        fi
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
          _current_hash=$(cat "$HOME/projects/CLAUDE.md" "$(pwd)/CLAUDE.md" 2>/dev/null | sha256sum | cut -d' ' -f1)
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
              _new_hash=$(cat "$HOME/projects/CLAUDE.md" "$(pwd)/CLAUDE.md" 2>/dev/null | sha256sum | cut -d' ' -f1)
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
        read -t 1 -r < /dev/null 2>/dev/null || true
      done) &
      watcher_pid=$!
    }}

    # Kill predecessor mosh-servers for this project prefix.
    # ai ps cron only kills score>=80 (needs 24h age), but rapid reconnects leave
    # ~60-score suspects accumulating for hours. Current mosh-server was just spawned
    # (<60s ago), so anything older with the same prefix is a stale predecessor.
    while read -r _mpid _mage; do
      [[ "$_mage" -gt 60 ]] && kill "$_mpid" 2>/dev/null || true
    done < <(ps -ax -o pid=,etime=,args= 2>/dev/null | grep "mosh-server" | grep -- "--project-prefix {project_prefix}" | \
      awk '{{pid=$1; etime=$2; n=split(etime,t,"[-:]"); if(n==4) s=t[1]*86400+t[2]*3600+t[3]*60+t[4]; else if(n==3) s=t[1]*3600+t[2]*60+t[3]; else s=t[1]*60+t[2]; print pid, s}}')
    unset _mpid _mage

    # Auto-clean orphaned processes at session start (score >= 80, local only).
    # Runs in foreground so orphans are gone before CC launches. Suppressed
    # when process_hygiene.auto_clean is false in config.toml.
    ai ps cron &>/dev/null || true

    # Auto-start sync watch and memory watch (PID files prevent duplicates)
    ai sync watch &>/dev/null &
    ai memory watch &>/dev/null &

    # Auto-start signal-watch for handoff auto-pickup (only for cc engine)
    if [[ "$engine" == "c" && -n "$project_name" ]]; then
      ai signal-watch start "$project_name" "$tmux_session" &>/dev/null
      signal_watch_pid=""
    fi

    # Auto-start quota-watch (idempotent — circusd skips if already registered)
    ai quota watch start 2>/dev/null || true

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

    # Export for CC Notification hook to use
    export ITERM2_SESSION_NUM="$_session_num"
    export ITERM2_SESSION_TYPE="$_session_type"

    trap 'kill "$watcher_pid" 2>/dev/null; ai signal-watch stop "$tmux_session" &>/dev/null; rm -f "$lock_file" "$_ai_state_dir/handoff-caught-$tmux_session" "$config_hash_file" "$config_changed_file"; ai internal cleanup-worktree "$ai_name" 2>/dev/null; ai internal release-color-slot "$ai_name" 2>/dev/null; ai internal cleanup-session-files "$ai_name" 2>/dev/null' EXIT

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
          exec zsh "$_script_stable_path"
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

      # On first run: synchronously drain local queue + NATS before launching CC.
      # Writes prompt_file if a pending handoff exists (local or cross-machine via NATS).
      # CC then launches with --continue on the task — zero user input required.
      if $first_run && [[ "$engine" == "c" && -n "$project_name" && ! -f "$prompt_file" ]]; then
        timeout 8 ai internal handoff-drain "$project_name" "$tmux_session" 2>/dev/null || true
      fi

      if [[ -f "$prompt_file" ]]; then
        resume_msg=$(cat "$prompt_file")
        rm -f "$prompt_file"
        if [[ "$engine" == "c" ]]; then
          claude $claude_perms_flag --continue "$resume_msg" --name "$ai_name"
        else
          (sleep 4; tmux send-keys -t "$tmux_session" "$resume_msg" C-m) &
          if [[ -n "$uuid" ]]; then {gemini_cmd} -y {sandbox_flag} -r "$uuid"
          else {gemini_cmd} -y {sandbox_flag} -i "/resume load $ai_name"
          fi
        fi
      else
        if [[ "$engine" == "c" ]]; then
          # Find the most recent conversation matching $ai_name by customTitle.
          # Touch it so --continue (which picks by mtime) resumes the right one.
          # --resume UUID opens a search picker instead of resuming directly, so avoid it.
          cc_project_dir="$HOME/.claude/projects/$(echo "$PWD" | sed 's|[/.]|-|g')"
          matched_file=$(python3 -c "
import json,os,sys
d,t=sys.argv[1],sys.argv[2]
if not os.path.isdir(d): sys.exit(0)
files=sorted([x for x in os.listdir(d) if x.endswith('.jsonl')],key=lambda x:os.path.getmtime(os.path.join(d,x)),reverse=True)
found=None
for fname in files:
    try:
        with open(os.path.join(d,fname)) as fh:
            for line in fh:
                r=json.loads(line);ct=r.get('customTitle','')
                if ct:
                    if ct==t: found=os.path.join(d,fname)
                    break
    except Exception: pass
    if found: break
if found: sys.stdout.write(found)
" "$cc_project_dir" "$ai_name" 2>/dev/null)
          if [[ -n "$matched_file" ]]; then
            touch "$matched_file" 2>/dev/null
            claude $claude_perms_flag --continue --name "$ai_name"
          elif [[ -d "$cc_project_dir" ]] && [[ -n "$(find "$cc_project_dir" -maxdepth 1 -name '*.jsonl' -print -quit 2>/dev/null)" ]]; then
            claude $claude_perms_flag --continue --name "$ai_name"
          else
            claude $claude_perms_flag --name "$ai_name"
          fi
        else
          if [[ -n "$uuid" ]]; then {gemini_cmd} -y {sandbox_flag} -r "$uuid"
          else {gemini_cmd} -y {sandbox_flag} -i "/resume load $ai_name"
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

      new_uuid=$(ai internal get-latest-gemini-id "$ai_name" 2>/dev/null)
      if [[ -n "$new_uuid" ]]; then
        uuid="$new_uuid"
        ai internal update-session-map g "$ai_name" "$uuid" 2>/dev/null
      fi

      first_run=false
      # Mark session as started so hot-reload exec skips first-run-only setup blocks.
      tmux set-environment -t "$tmux_session" AI_SESSION_STARTED 1 2>/dev/null || true
      elapsed=$_exit_elapsed
      if (( elapsed < 3 )); then
        echo "AI CLI exited too quickly ($elapsed s) — stopping. Run 'ai c' to retry."
        break
      fi
      _iterm2_status "resuming" "$_session_type" "$tmux_session"
      if [[ -f "$handoff_pending_file" ]]; then
        pending_msg=$(cat "$handoff_pending_file")
        rm -f "$handoff_pending_file"
        echo "$pending_msg" > "$prompt_file"
        printf '{{"event":"handoff.while_loop_pickup","session":"%s","ts":%s}}\n' \
          "$tmux_session" "$(date +%s)" >> "$_ai_state_dir/handoff-events.jsonl" 2>/dev/null || true
      fi
      # Self-update: if ai-cli was reinstalled, exec a fresh template so new changes take effect.
      # exec bash <new-script> replaces only this bash process inside the tmux window;
      # mosh connects to the tmux session (not this PID) so it is unaffected.
      _current_ver=$(ai internal get-version 2>/dev/null || echo "unknown")
      if [[ "$_current_ver" != "unknown" && "$_current_ver" != "$_template_version" ]]; then
        echo "ai-cli updated ($_template_version → $_current_ver) — reloading session template..."
        _refresh_script=$(ai internal refresh-template "$tmux_session" 2>/dev/null)
        if [[ -n "$_refresh_script" && -f "$_refresh_script" ]]; then
          exec bash "$_refresh_script"
        else
          echo "Template refresh failed — run 'ai c $ai_name' to get new template"
          _template_version="$_current_ver"
        fi
      fi
      echo "Resuming... (Ctrl-C to exit)"
      sleep 0.5 || break
    done
    (ai internal publish-event "$tmux_session" "STOP" 2>/dev/null || true) &
    (ai internal publish-session-event "$tmux_session" "stopped" 2>/dev/null || true) &
    {('echo "Session ended. Exit shell to close tmux session."; exec $SHELL') if is_remote else "exit 0"}
    """
    return script
