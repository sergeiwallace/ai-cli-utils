import argparse
import sys
import os
import json
import shutil
import tempfile
import time
import subprocess
import re
import shlex
import urllib.request
from pathlib import Path

from .config import (  # noqa: F401 — re-exported for backwards compatibility with tests
    DEFAULT_CONFIG,
    WORKTREE_DIR,
    _find_project_dir,
    _get_handoff_queue_dir,
    _get_main_project_dir,
    _get_main_project_name,
    _get_project_prefix_by_name,
    _get_project_registry_path,
    _get_projects_dir,
    _migrate_xdg_dir,
    get_current_project_name,
    get_project_aliases,
    get_session_map,
    get_session_map_path,
    get_xdg_cache_home,
    get_xdg_config_home,
    get_xdg_state_home,
    load_config,
    load_project_registry,
    save_session_map,
    validate_registry_completeness,
)
from .iterm2 import (  # noqa: F401 — re-exported for backwards compatibility with tests
    _DEFAULT_ITERM2_CONFIG,
    _assign_iterm2_color_slot,
    _configure_tmux_for_iterm2,
    _emit_iterm2_profile_setup,
    _is_iterm2,
    _iterm2_palette,
    _iterm2_session_type,
    _iterm2_state_dir,
    _load_iterm2_config,
    _release_iterm2_color_slot,
    _resolve_iterm2_config,
)
from .handoff import (  # noqa: F401 — re-exported for backwards compatibility with tests
    _claim_handoff_for_signal,
    _find_best_handoff,
    _format_handoff_summary,
    _log_handoff_event,
    check_handoff,
    check_handoff_project,
    claim_handoff,
    complete_handoff,
    post_handoff,
)
from .session import (  # noqa: F401 — re-exported for backwards compatibility with tests
    _AI_SESSION_RE,
    _checkpoint_to_chat_uuid,
    _convert_checkpoint_to_chat,
    _find_latest_gemini_uuid,
    _get_chat_last_message_timestamp,
    _resolve_is_remote,
    _sweep_stale_iterm2_profiles,
    build_session_name,
    cleanup_stale_sessions,
    cleanup_worktree,
    create_worktree,
    detect_repo_root,
    find_next_index,
    find_recent_session,
    get_latest_gemini_session_id,
    get_project_prefix,
    resolve_session,
)
from .transport import (  # noqa: F401 — re-exported for backwards compatibility with tests
    _ensure_tailscale_up,
    _ensure_vpn_watcher,
    _is_vpn_active,
    _maybe_stop_vpn_watcher,
    _monotonic,
    _run_transport_loop,
    _write_transport_state,
)

# MAINTENANCE: when editing ai-cli, also update:
#   - docs/tools/ai-cli-usage.md (usage reference, session naming, transport, auto-resume)
#   - README.md (if CLI interface changes)
#   - Code comments in this file (especially around session naming, resume logic, mosh/transport)
#   - CLAUDE.md ai-cli deploy note (reinstall in 3 places: Mac uv tool, server uv tool, extra_venvs)


# --- Script Generation ---


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

    script = f"""
    {cd_cmd}
    first_run=true
    ai_name="{ai_name}"
    engine="{engine}"
    tmux_session="{session}"
    _template_version="{_template_version}"
    uuid="{session_id_uuid or ""}"
    project_prefix="{project_prefix}"
    project_name="{project_name}"
    _ai_state_dir="$HOME/.local/state/ai-cli-utils"
    mkdir -p "$_ai_state_dir/iterm2"

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
          # last visible line). Three layers of protection against false positives:
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
          # 3. signal_file deleted AFTER injection (not before): preserves retry
          #    semantics if the watcher is killed mid-sequence.
          #
          # C-u removed: the guard already confirms an empty prompt; C-u is
          # redundant and has unknown behavior in CC's React/Ink TUI.
          # sleep 0.5 removed: eliminated the race window between check and action.
          if (( counter >= 10 )); then
            _sig_last=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1)
            if echo "$_sig_last" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
              _sig_verify=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1)
              if echo "$_sig_verify" | grep -qE '^[[:space:]]*❯[[:space:]]*$'; then
                if [[ "$engine" == "g" ]]; then
                  tmux send-keys -t "$tmux_session" "/resume save $ai_name" C-m
                  sleep 2
                fi
                tmux send-keys -t "$tmux_session" '/exit' C-m
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

    _iterm2_fleet_setup() {{
      [[ "$LC_TERMINAL" != "iTerm2" && "$TERM_PROGRAM" != "iTerm.app" ]] && return 0
      local sname="$1"
      # Profile name is deterministic — matches the Dynamic Profile generated by Python
      local _profile="ai-cli:$ai_name"
      _it2 "\\033]1337;SetProfile=$_profile\\007"
      _it2 "\\033]1337;SetColors=tab=$_iterm2_color\\007"
      # OSC 1 sets the iTerm2 "Name" field; mosh does not intercept it
      _it2 "\\033]1;$sname\\007"
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
      _it2 "\\033]1;${{type_sym}}${{sym}}$sname\\007"
    }}

    # Extract session number from ai_name (e.g., "sw-3" → "3") for downstream hooks.
    _session_num=$(echo "$ai_name" | grep -oE '[0-9]+$' || echo "1")
    _session_type="cc"
    [[ "$engine" == "g" ]] && _session_type="gemini"
    _iterm2_fleet_setup "$tmux_session"

    # Export for CC Notification hook to use
    export ITERM2_SESSION_NUM="$_session_num"
    export ITERM2_SESSION_TYPE="$_session_type"

    trap 'kill "$watcher_pid" 2>/dev/null; ai signal-watch stop "$tmux_session" &>/dev/null; rm -f "$lock_file" "$_ai_state_dir/handoff-caught-$tmux_session" "$config_hash_file" "$config_changed_file"; ai internal cleanup-worktree "$ai_name" 2>/dev/null; ai internal release-color-slot "$ai_name" 2>/dev/null; ai internal cleanup-session-files "$ai_name" 2>/dev/null' EXIT

    while true; do
      start_watcher
      start_ts=$(date +%s)
      # Re-emit iTerm2 setup + set status to running
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
        ai internal handoff-drain "$project_name" "$tmux_session" 2>/dev/null || true
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
      # Self-update: if ai-cli was reinstalled, note it and continue (exec restart breaks mosh sessions)
      _current_ver=$(ai internal get-version 2>/dev/null || echo "unknown")
      if [[ "$_current_ver" != "unknown" && "$_current_ver" != "$_template_version" ]]; then
        echo "ai-cli updated ($_template_version → $_current_ver) — run 'ai c $ai_name' to get new template"
        _template_version="$_current_ver"
      fi
      echo "Resuming... (Ctrl-C to exit)"
      sleep 0.5 || break
    done
    (ai internal publish-event "$tmux_session" "STOP" 2>/dev/null || true) &
    (ai internal publish-session-event "$tmux_session" "stopped" 2>/dev/null || true) &
    {('echo "Session ended. Exit shell to close tmux session."; exec $SHELL') if is_remote else "exit 0"}
    """
    return script


# --- Subcommands ---


def _find_aicli_project_path(config: dict) -> "Path | None":
    """Locate the ai-cli-utils source tree regardless of cwd.

    Priority:
    1. [deploy] project_path from config
    2. Package __file__ location (dev-editable install)
    """
    cfg_path = config.get("deploy", {}).get("project_path", "")
    if cfg_path:
        return Path(cfg_path).expanduser()
    try:
        import importlib.util

        spec = importlib.util.find_spec("ai_cli")
        if spec and spec.origin:
            pkg_dir = Path(spec.origin).parent  # …/ai_cli/
            for candidate in (pkg_dir.parent, pkg_dir.parent.parent):
                pyproject = candidate / "pyproject.toml"
                if pyproject.exists() and "ai-cli-utils" in pyproject.read_text():
                    return candidate
    except Exception:
        pass
    # cwd fallback — valid when the user is already in the project directory.
    # Check the project name specifically to avoid matching projects that merely
    # depend on ai-cli-utils.
    cwd = Path.cwd()
    cwd_pyproject = cwd / "pyproject.toml"
    if cwd_pyproject.exists():
        content = cwd_pyproject.read_text()
        if re.search(r'^name\s*=\s*["\']ai-cli-utils["\']', content, re.MULTILINE):
            return cwd
    return None


def _deploy_cc_config_files(project_path: Path) -> None:
    """Copy bundled CC config files from the package data dir to ~/.claude/.

    Writes plain files, replacing any pre-existing symlinks. These files are
    owned by ai-cli-utils and must not be managed by ai sync or tracked in any
    project git repo.
    """
    data_dir = project_path / "src" / "ai_cli" / "data"
    cc_dir = Path.home() / ".claude"

    # Files to deploy: (source relative to data_dir, dest relative to ~/.claude/)
    deployable = [
        ("statusline-command.sh", "statusline-command.sh"),
    ]

    for src_name, dst_rel in deployable:
        src = data_dir / src_name
        if not src.exists():
            continue
        dst = cc_dir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Unlink first so we replace any symlink with a plain file
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        import shutil as _shutil

        _shutil.copy2(src, dst)
        if src.suffix == ".sh":
            dst.chmod(dst.stat().st_mode | 0o755)


def _auto_update_if_stale(config: dict) -> None:
    """Run `ai update --force` if the project has new commits since the last update."""
    project_path = _find_aicli_project_path(config)
    if project_path is None or not (project_path / "pyproject.toml").exists():
        return
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_path, capture_output=True, text=True)
    if head.returncode != 0:
        return
    current_hash = head.stdout.strip()
    stamp_file = get_xdg_state_home() / "last_update_commit.txt"
    if stamp_file.exists() and stamp_file.read_text().strip() == current_hash:
        return
    print("ai-cli-utils has new commits — running ai update --force...")
    ai_bin = shutil.which("ai") or "ai"
    result = subprocess.run([ai_bin, "update", "--force"], cwd=project_path)
    if result.returncode != 0:
        print("Warning: auto-update failed, continuing with current version", file=sys.stderr)


def trigger_background_update():
    state_file = get_xdg_state_home() / "update_check.json"
    now = time.time()
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            if now - state.get("last_checked", 0) < 3600 * 24:
                return
        except Exception:
            pass
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_checked": now}))
    subprocess.Popen(
        ["uv", "tool", "upgrade", "ai-cli-utils"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# --- SSH tunnel management (autossh-backed) ---


def _cmd_tunnel_start(
    local_port: int, remote_port: int, *, forward: bool = True, config: dict, quiet: bool = False
) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{local_port}.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # 0 = check existence only
            if not quiet:
                print(f"Tunnel already running: localhost:{local_port} (PID {pid})")
            return
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    autossh_bin = shutil.which("autossh")
    if not autossh_bin:
        print(
            "autossh not found. Install it first:\n  macOS:  brew install autossh\n  Linux:  apt install autossh",
            file=sys.stderr,
        )
        sys.exit(1)

    remote_cfg = config.get("remote", {})
    host = remote_cfg.get("host", "")
    user = remote_cfg.get("user", "ubuntu")
    if not host:
        print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)
    # Use vpn_host when VPN is active — Tailscale becomes unreachable under VPN.
    vpn_host = remote_cfg.get("vpn_host", "") or host
    if vpn_host != host and _is_vpn_active():
        host = vpn_host

    direction = "-L" if forward else "-R"
    cmd = [
        autossh_bin,
        "-M",
        "0",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        direction,
        f"{remote_port}:localhost:{local_port}",
        f"{user}@{host}",
    ]
    proc = subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(proc.pid))
    if not quiet:
        print(f"Tunnel started: localhost:{local_port} -> {host}:{remote_port} (PID {proc.pid})")


def _ensure_nats_tunnel(config: dict) -> None:
    """Auto-start NATS tunnel if [messaging] tunnel_port is configured and tunnel isn't running."""
    tunnel_port = config.get("messaging", {}).get("tunnel_port")
    if not tunnel_port:
        return
    port = int(tunnel_port)
    # Check if already running before starting — no sleep needed if already up
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{port}.pid"
    already_running = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            already_running = True
        except (ProcessLookupError, ValueError):
            pass
    try:
        _cmd_tunnel_start(port, port, forward=True, config=config, quiet=True)
    except SystemExit:
        return  # missing autossh or remote config — skip silently
    if not already_running:
        # Give SSH time to establish before handoff-drain tries to connect
        time.sleep(3)


def _cmd_tunnel_stop(local_port: int) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{local_port}.pid"
    if not pid_file.exists():
        return
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        pass
    pid_file.unlink(missing_ok=True)
    print(f"Tunnel stopped: port {local_port}")


def _cmd_tunnel_status() -> None:
    state_dir = get_xdg_state_home()
    pid_files = sorted(state_dir.glob("tunnel-*.pid"))
    if not pid_files:
        print("No tunnels registered.")
        return
    for pid_file in pid_files:
        port = pid_file.stem[len("tunnel-") :]
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            status = "alive"
        except ProcessLookupError:
            status = "dead"
            pid_file.unlink(missing_ok=True)
        print(f"port {port}: PID {pid} ({status})")


# --- CDP (Chrome DevTools Protocol) browser management ---


def _find_chrome_binary(config: dict) -> str | None:
    """Return path to Chrome/Chromium binary, or None if not found."""
    configured = config.get("cdp", {}).get("binary_path", "")
    if configured:
        return str(configured) if Path(configured).exists() else None

    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        candidates = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]

    for c in candidates:
        found = shutil.which(c)
        if found:
            return found
        if Path(c).exists():
            return c
    return None


def _cmd_cdp_start(port: int, incognito: bool, config: dict, tunnel: bool = False, forward: bool = False) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"cdp-{port}.pid"

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            print(f"CDP already running on port {port} (PID {pid})")
            return
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    chrome = _find_chrome_binary(config)
    if not chrome:
        print(
            "Chrome/Chromium not found. Install it or set [cdp] binary_path in config.",
            file=sys.stderr,
        )
        sys.exit(1)

    user_data_dir = Path(tempfile.gettempdir()) / f"chrome-debug-{port}"
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
    ]
    if incognito:
        cmd.append("--incognito")

    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(proc.pid))

    url = f"http://localhost:{port}/json/version"
    deadline = time.monotonic() + 5.0
    ready = False
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)  # noqa: S310
            ready = True
            break
        except Exception:
            time.sleep(0.25)

    if ready:
        print(f"CDP ready at localhost:{port}")
    else:
        print(f"CDP started (PID {proc.pid}) — endpoint not yet responding on port {port}")

    if tunnel:
        _cmd_tunnel_start(port, port, forward=forward, config=config)


def _cmd_cdp_stop(port: int, tunnel: bool = False) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"cdp-{port}.pid"
    if not pid_file.exists():
        print(f"No CDP process registered on port {port}.")
        return
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        pass
    pid_file.unlink(missing_ok=True)
    print(f"CDP stopped: port {port}")
    if tunnel:
        _cmd_tunnel_stop(port)


def _cmd_cdp_status() -> None:
    state_dir = get_xdg_state_home()
    pid_files = sorted(state_dir.glob("cdp-*.pid"))
    if not pid_files:
        print("No CDP processes registered.")
        return
    for pid_file in pid_files:
        port = pid_file.stem[len("cdp-") :]
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            status = "alive"
        except ProcessLookupError:
            status = "dead"
            pid_file.unlink(missing_ok=True)
        print(f"port {port}: PID {pid} ({status})")


# --- Circus / signal-watch process management ---


def _ensure_circusd() -> str:
    """Start circusd if not already running. Returns the endpoint URI."""
    import shutil as _shutil
    import time as _time

    state_dir = get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    endpoint = f"ipc://{state_dir}/circus.endpoint"

    # Check PID file first — if the PID is dead, clean up stale socket files so
    # CircusClient doesn't hang connecting to a dead IPC socket (ZMQ connects to
    # the file but nobody's listening, and send_message blocks indefinitely).
    pid_file = state_dir / "circusd.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # raises ProcessLookupError if dead
        except (ValueError, ProcessLookupError, PermissionError):
            for _stale in ("circus.endpoint", "circus.pubsub", "circusd.pid"):
                (state_dir / _stale).unlink(missing_ok=True)

    # Try existing daemon first
    endpoint_sock = state_dir / "circus.endpoint"
    if endpoint_sock.exists():
        try:
            from circus.client import CircusClient

            CircusClient(endpoint=endpoint, timeout=1.0).send_message("status")
            return endpoint
        except Exception:
            pass

    # Write circus.ini
    ini_path = state_dir / "circus.ini"
    ini_path.write_text(
        f"[circus]\n"
        f"endpoint        = {endpoint}\n"
        f"pubsub_endpoint = ipc://{state_dir}/circus.pubsub\n"
        f"logoutput       = {state_dir}/circus.log\n"
        f"umask           = 0o022\n"
    )

    circusd_bin = _shutil.which("circusd") or str(Path.home() / ".local" / "bin" / "circusd")
    pidfile = str(state_dir / "circusd.pid")
    subprocess.Popen(
        [circusd_bin, "--daemon", "--pidfile", pidfile, str(ini_path)],
    )

    # Poll until ready
    from circus.client import CircusClient

    for _ in range(10):
        _time.sleep(0.3)
        try:
            CircusClient(endpoint=endpoint, timeout=1.0).send_message("status")
            return endpoint
        except Exception:
            pass

    raise RuntimeError("circusd did not start in time")


def _cmd_signal_watch_start(project: str, session: str) -> None:
    endpoint = _ensure_circusd()
    from circus.client import CircusClient

    client = CircusClient(endpoint=endpoint, timeout=5.0)
    watcher_name = f"sw-{session}"
    ai_bin = shutil.which("ai") or "ai"
    cmd = f"{ai_bin} internal signal-watch {project} {session}"

    # Remove existing watcher idempotently
    try:
        client.send_message("rm", name=watcher_name)
    except Exception:
        pass

    client.send_message(
        "add",
        name=watcher_name,
        cmd=cmd,
        options={
            "copy_env": True,
            "respawn": False,
            "singleton": True,
        },
        start=True,
    )


def _cmd_signal_watch_stop(session: str) -> None:
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        CircusClient(endpoint=endpoint, timeout=2.0).send_message("rm", name=f"sw-{session}")
    except Exception:
        pass


def _cmd_signal_watch_status() -> None:
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        result = CircusClient(endpoint=endpoint, timeout=2.0).send_message("status")
        statuses = result.get("statuses", {}) if isinstance(result, dict) else {}
        sw_watchers = {k: v for k, v in statuses.items() if k.startswith("sw-")}
        if not sw_watchers:
            print("No signal-watch processes running.")
            return
        for name, status in sorted(sw_watchers.items()):
            session = name[len("sw-") :]
            print(f"{session}: {status}")
    except Exception:
        print("circusd not running.")


# --- CLI Entry Point ---


def cli():
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        try:
            from importlib.metadata import version as _pkg_version

            print(_pkg_version("ai-cli-utils"))
        except Exception:
            print("unknown")
        sys.exit(0)

    config = load_config()

    if len(sys.argv) > 1 and sys.argv[1] == "internal":
        if len(sys.argv) < 3:
            print("Usage: ai internal <action> [args...]", file=sys.stderr)
            sys.exit(1)
        action = sys.argv[2]
        if action == "get-latest-gemini-id":
            _ai_name_arg = sys.argv[3] if len(sys.argv) > 3 else None
            res = get_latest_gemini_session_id(_ai_name_arg)
            if res:
                print(res)
            sys.exit(0)
        elif action == "update-session-map":
            if len(sys.argv) < 6:
                print("Usage: ai internal update-session-map <engine> <ai_name> <uuid>", file=sys.stderr)
                sys.exit(1)
            engine, ai_name, uuid = sys.argv[3], sys.argv[4], sys.argv[5]
            d = get_session_map(engine)
            d[ai_name] = uuid
            save_session_map(d, engine)
            sys.exit(0)
        elif action == "cleanup-worktree":
            if len(sys.argv) < 4:
                print("Usage: ai internal cleanup-worktree <ai_name>", file=sys.stderr)
                sys.exit(1)
            cleanup_worktree(sys.argv[3])
            sys.exit(0)
        elif action == "release-color-slot":
            if len(sys.argv) < 4:
                print("Usage: ai internal release-color-slot <ai_name>", file=sys.stderr)
                sys.exit(1)
            _release_iterm2_color_slot(sys.argv[3])
            sys.exit(0)
        elif action == "cleanup-session-files":
            if len(sys.argv) < 4:
                print("Usage: ai internal cleanup-session-files <ai_name>", file=sys.stderr)
                sys.exit(1)
            from . import icon_generator as _ig_cs

            _ig_cs.cleanup_session_files(sys.argv[3])
            sys.exit(0)
        elif action == "get-version":
            try:
                from importlib.metadata import version as _pkg_version

                print(_pkg_version("ai-cli-utils"))
            except Exception:
                print("unknown")
            sys.exit(0)
        elif action == "notify":
            if len(sys.argv) < 5:
                print("Usage: ai internal notify <session_id> <message>", file=sys.stderr)
                sys.exit(1)
            from .notifications import NotificationManager

            session_id = sys.argv[3]
            msg = sys.argv[4]
            NotificationManager(session_id).notify(msg)
            sys.exit(0)
        elif action == "publish-event":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish-event <session_id> <event_type>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            session_id = sys.argv[3]
            event_type = sys.argv[4]
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish_event(session_id, event_type))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)
        elif action == "publish-heartbeat":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish-heartbeat <session_id> <data_json>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            session_id = sys.argv[3]
            try:
                data = json.loads(sys.argv[4])
            except json.JSONDecodeError as e:
                print(f"Invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish_heartbeat(session_id, data))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)
        elif action == "publish-session-event":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish-session-event <session_id> <started|stopped>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            session_id = sys.argv[3]
            event_verb = sys.argv[4]  # "started" or "stopped"
            subject = f"session.{session_id}.{event_verb}"
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish(subject, {"session_id": session_id, "event": event_verb, "ts": time.time()}))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)
        elif action == "publish":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish <subject> <json_payload>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            subject = sys.argv[3]
            try:
                payload = json.loads(sys.argv[4])
            except (json.JSONDecodeError, IndexError):
                payload = {}
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish(subject, payload))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)
        elif action == "signal-watch":
            # ai internal signal-watch <project> <session_id>
            if len(sys.argv) < 5:
                print("Usage: ai internal signal-watch <project> <session_id>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            sw_project = sys.argv[3]
            sw_session_id = sys.argv[4]
            sw_handoff_dir = _get_handoff_queue_dir()
            sw_pending_file = get_xdg_state_home() / f"handoff-pending-{sw_session_id}"
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            sw_client = NATSClient(servers=nats_servers)

            # Not covered: entire _on_handoff closure is only invoked when a live NATS
            # JetStream message arrives. Requires a real NATS server + network delivery.
            # Inner exception branches (OSError on file write, ValueError on filename parse)
            # additionally require specific filesystem failure conditions inside a live
            # async callback. See docs/test/unit-tests.md §Intentionally Uncovered Lines.
            async def _on_handoff(data):
                handoff_id = data.get("id")
                title = data.get("title", "")
                priority = data.get("priority", "")
                message = data.get("message", "")
                for_machine = data.get("for_machine", "")
                if not for_machine or for_machine != os.environ.get("AI_CLI_HOST", ""):
                    return  # not intended for this machine
                print(f"\n[HANDOFF] {priority} #{handoff_id}: {title}", flush=True)
                if sw_handoff_dir is None or not handoff_id:
                    return
                # Cross-machine delivery: if file doesn't exist locally but payload carries content, write it
                content = data.get("content")
                filename = data.get("filename")
                if content and filename:
                    pending_dir = sw_handoff_dir / "pending"
                    claimed_dir = sw_handoff_dir / "claimed"
                    local_file = pending_dir / filename
                    # Skip if already claimed in a previous session
                    if (claimed_dir / filename).exists():
                        return
                    if not local_file.exists():
                        pending_dir.mkdir(parents=True, exist_ok=True)
                        try:
                            local_file.write_text(content)
                        except OSError:
                            pass
                claimed = _claim_handoff_for_signal(sw_handoff_dir, int(handoff_id), sw_session_id)
                if claimed is None:
                    return  # another session claimed it first
                _log_handoff_event(
                    "handoff.claimed",
                    handoff_id=handoff_id,
                    session=sw_session_id,
                    layer="nats_realtime" if data.get("_source") != "startup_scan" else "startup_scan",
                )
                resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
                sw_pending_file.parent.mkdir(parents=True, exist_ok=True)
                sw_pending_file.write_text(resume_msg)
                # Touch signal_file to wake the watcher. The watcher's idle guard
                # (counter >= 10 + double ❯ check) ensures /exit is only injected
                # when CC is at the empty prompt — safe to touch unconditionally here.
                # Without this, the pending file sits unread until CC exits naturally.
                sw_signal_file = get_xdg_state_home() / f"cc-exit-{sw_session_id}"
                try:
                    sw_signal_file.touch()
                except OSError:
                    pass

            # Startup scan: pick up any unclaimed files already in the pending queue
            if sw_handoff_dir is not None:
                pending_dir = sw_handoff_dir / "pending"
                if pending_dir.exists():
                    for f in sorted(pending_dir.glob("*.md")):
                        try:
                            fid = int(f.name.split("-")[0])
                        except ValueError:
                            continue
                        try:
                            raw = f.read_text()
                            fm_title = re.search(r'^title:\s*"?([^"\n]+)"?', raw, re.MULTILINE)
                            fm_priority = re.search(r"^priority:\s*(\S+)", raw, re.MULTILINE)
                            fm_for_machine = re.search(r"^for_machine:\s*(\S+)", raw, re.MULTILINE)
                            body = raw.split("---", 2)[-1].strip() if raw.count("---") >= 2 else ""
                            scan_title = fm_title.group(1).strip() if fm_title else f.stem
                            scan_priority = fm_priority.group(1) if fm_priority else ""
                            scan_for_machine = fm_for_machine.group(1) if fm_for_machine else ""
                        except OSError:
                            scan_title, scan_priority, body, scan_for_machine = f.stem, "", "", ""
                        asyncio.run(
                            _on_handoff(
                                {
                                    "id": fid,
                                    "title": scan_title,
                                    "priority": scan_priority,
                                    "message": body,
                                    "for_machine": scan_for_machine,
                                    "_source": "startup_scan",
                                }
                            )
                        )

            consumer_name = f"{sw_session_id}-signal-watcher"

            async def _run_subscriptions() -> None:
                await sw_client.subscribe_durable(f"handoff.{sw_project}", consumer_name, _on_handoff)

            try:
                asyncio.run(_run_subscriptions())
            except Exception:
                # Not covered: _run_subscriptions blocks indefinitely on success; exception
                # path requires a live NATS server to fail mid-subscription.
                pass
            sys.exit(0)
        elif action == "quota-subscriber":
            # ai internal quota-subscriber
            # Persistent daemon: subscribes to quota.snapshot via JetStream durable consumer.
            # Runs as a Circus-managed process on Mac, independent of CC session lifecycle.
            # Missed messages during downtime are replayed on reconnect (JetStream durability).
            import asyncio
            from .messaging import NATSClient

            qs_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            qs_client = NATSClient(servers=qs_servers)

            # Not covered: _on_quota_snapshot_msg is only invoked on live JetStream delivery.
            # Requires a real NATS server + published quota.snapshot message.
            async def _on_quota_snapshot_msg(data: dict) -> None:
                from .quota_db import record_quota_snapshot

                try:
                    record_quota_snapshot(
                        usage_percent=data["usage_percent"],
                        session_pct=data.get("session_pct"),
                        weekly_sonnet_pct=data.get("weekly_sonnet_pct"),
                        extra_pct=data.get("extra_pct"),
                        reset_at=data.get("reset_at"),
                    )
                except Exception:
                    pass

            try:
                asyncio.run(
                    qs_client.subscribe_durable(
                        "quota.snapshot",
                        "quota-subscriber-mac",
                        _on_quota_snapshot_msg,
                    )
                )
            except Exception:
                # Not covered: subscribe_durable blocks indefinitely on success; exception
                # path requires a live NATS server to fail mid-subscription.
                pass
            sys.exit(0)
        elif action == "handoff-drain":
            # ai internal handoff-drain <project> <session_id>
            # Synchronous: drain pending NATS messages + local file scan, then exit.
            # Called BEFORE CC launches so prompt_file is ready on first invocation.
            if len(sys.argv) < 5:
                sys.exit(0)
            import asyncio
            from .messaging import NATSClient

            hd_project = sys.argv[3]
            hd_session = sys.argv[4]
            hd_handoff_dir = _get_handoff_queue_dir()
            hd_prompt_file = get_xdg_state_home() / f"cc-resume-prompt-{hd_session}"
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            hd_client = NATSClient(servers=nats_servers)
            _log_handoff_event("handoff.drain.started", session=hd_session, project=hd_project)

            # Not covered: _write_pending_if_claimed is only reachable via _drain() which
            # requires a live NATS JetStream connection, or from the local-scan path which
            # is covered. Inner branches (for_machine mismatch, hd_handoff_dir is None,
            # cross-machine file write, claimed is None) all require live handoff delivery
            # or specific filesystem failure conditions inside an async context.
            # See docs/test/unit-tests.md §Intentionally Uncovered Lines.
            def _write_pending_if_claimed(data):
                handoff_id = data.get("id")
                title = data.get("title", "")
                priority = data.get("priority", "")
                message = data.get("message", "")
                for_machine = data.get("for_machine", "")
                if not for_machine or for_machine != os.environ.get("AI_CLI_HOST", ""):
                    return False  # not intended for this machine
                if hd_handoff_dir is None or not handoff_id:
                    return False
                # Cross-machine: write file locally from payload if missing
                content = data.get("content")
                filename = data.get("filename")
                if content and filename:
                    pending_dir = hd_handoff_dir / "pending"
                    claimed_dir = hd_handoff_dir / "claimed"
                    local_file = pending_dir / filename
                    # Skip if already claimed in a previous session
                    if (claimed_dir / filename).exists():
                        return False
                    if not local_file.exists():
                        pending_dir.mkdir(parents=True, exist_ok=True)
                        try:
                            local_file.write_text(content)
                        except OSError:
                            return False
                claimed = _claim_handoff_for_signal(hd_handoff_dir, int(handoff_id), hd_session)
                if claimed is None:
                    return False
                _log_handoff_event(
                    "handoff.claimed",
                    handoff_id=handoff_id,
                    session=hd_session,
                    layer="pre_launch_drain",
                )
                resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
                hd_prompt_file.parent.mkdir(parents=True, exist_ok=True)
                hd_prompt_file.write_text(resume_msg)
                return True

            # 1. Local file scan first (fast, no network)
            if hd_handoff_dir is not None:
                pending_dir = hd_handoff_dir / "pending"
                if pending_dir.exists():
                    best = _find_best_handoff(pending_dir, project_filter=hd_project)
                    if best is not None:
                        try:
                            fid = int(best.name.split("-")[0])
                            raw = best.read_text()
                            fm_title = re.search(r'^title:\s*"?([^"\n]+)"?', raw, re.MULTILINE)
                            fm_priority = re.search(r"^priority:\s*(\S+)", raw, re.MULTILINE)
                            fm_for_machine = re.search(r"^for_machine:\s*(\S+)", raw, re.MULTILINE)
                            body = raw.split("---", 2)[-1].strip() if raw.count("---") >= 2 else ""
                            local_for_machine = fm_for_machine.group(1) if fm_for_machine else ""
                            _log_handoff_event(
                                "handoff.drain.local_found",
                                session=hd_session,
                                handoff_id=fid,
                                for_machine=local_for_machine,
                            )
                            _write_pending_if_claimed(
                                {
                                    "id": fid,
                                    "title": fm_title.group(1).strip() if fm_title else best.stem,
                                    "priority": fm_priority.group(1) if fm_priority else "",
                                    "message": body,
                                    "for_machine": local_for_machine,
                                }
                            )
                        except Exception:
                            # Not covered: requires filesystem error reading a pending
                            # handoff file that exists and was just discovered by glob.
                            pass

            # 2. NATS drain: pull pending JetStream messages (non-blocking, 2s timeout)
            if not hd_prompt_file.exists():
                _log_handoff_event("handoff.drain.nats_attempt", session=hd_session, project=hd_project)

                # Not covered: _drain() is an async closure that requires a live NATS
                # JetStream server. Inner branches (js is None, message decode error,
                # _write_pending_if_claimed returning True, fetch timeout, subscribe
                # failure) all require specific live-server or network-failure conditions.
                # See docs/test/unit-tests.md §Intentionally Uncovered Lines.
                async def _drain():
                    try:
                        await hd_client.connect()
                    except Exception as e:
                        _log_handoff_event("handoff.drain.nats_connect_failed", session=hd_session, error=str(e))
                        return
                    if not hd_client.js:
                        _log_handoff_event("handoff.drain.nats_no_js", session=hd_session)
                        return
                    consumer_name = f"{hd_session}-pre-launch"
                    subject = f"handoff.{hd_project}"
                    try:
                        await hd_client._ensure_stream(subject)
                        sub = await hd_client.js.pull_subscribe(subject, durable=consumer_name)
                        while True:
                            try:
                                msgs = await sub.fetch(1, timeout=2)
                                for msg in msgs:
                                    try:
                                        data = json.loads(msg.data.decode())
                                    except Exception:
                                        data = {}
                                    await msg.ack()
                                    if _write_pending_if_claimed(data):
                                        return
                            except Exception:
                                break
                    except Exception as e:
                        _log_handoff_event("handoff.drain.nats_subscribe_failed", session=hd_session, error=str(e))
                    finally:
                        await hd_client.close()

                try:
                    asyncio.run(_drain())
                except Exception as e:
                    # Not covered: requires asyncio.run() itself to raise, which needs a
                    # broken event loop or NATS server in a specific failure state.
                    _log_handoff_event("handoff.drain.nats_run_failed", session=hd_session, error=str(e))

            sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "upgrade":
        print("Upgrading ai-cli-utils...", file=sys.stderr)
        os.execvp("uv", ["uv", "tool", "upgrade", "ai-cli-utils"])

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        from .setup import run_setup

        sys.exit(run_setup())

    if len(sys.argv) > 1 and sys.argv[1] == "handoff":
        if len(sys.argv) == 2:
            print("Usage: ai handoff [post|check|claim|complete]")
            sys.exit(1)
        action = sys.argv[2]
        if action == "post":
            post_args = sys.argv[3:]
            if "--remote" in post_args or "-R" in post_args:
                post_args = [a for a in post_args if a not in ("--remote", "-R")]
                remote_cfg = load_config().get("remote", {})
                remote_host = remote_cfg.get("host", "")
                remote_user = remote_cfg.get("user", "ubuntu")
                if not remote_host:
                    print("Error: [remote] host not set in config", file=sys.stderr)
                    sys.exit(1)
                os.execvp("ssh", ["ssh", f"{remote_user}@{remote_host}", "ai", "handoff", "post"] + post_args)
            for_machine = None
            for flag in ("--for-machine", "-m"):
                if flag in post_args:
                    idx = post_args.index(flag)
                    for_machine = post_args[idx + 1]
                    post_args = post_args[:idx] + post_args[idx + 2 :]
                    break
            if not for_machine:
                print("Error: --for-machine <machine> is required", file=sys.stderr)
                sys.exit(1)
            if len(post_args) < 4:
                print(
                    "Usage: ai handoff post --for-machine <machine> <title> <priority> <project> <message>",
                    file=sys.stderr,
                )
                sys.exit(1)
            post_handoff(post_args[0], post_args[1], post_args[2], post_args[3], for_machine=for_machine)
        elif action == "check":
            check_handoff()
        elif action == "check-project":
            if len(sys.argv) < 4:
                print("Usage: ai handoff check-project <project_name>", file=sys.stderr)
                sys.exit(1)
            check_handoff_project(sys.argv[3])
        elif action == "claim":
            if len(sys.argv) < 4:
                print("Usage: ai handoff claim <file_path>", file=sys.stderr)
                sys.exit(1)
            claim_handoff(sys.argv[3])
        elif action == "complete":
            if len(sys.argv) < 4:
                print("Usage: ai handoff complete <file_path>", file=sys.stderr)
                sys.exit(1)
            complete_handoff(sys.argv[3])
        else:
            print(f"Error: unknown handoff action '{action}'", file=sys.stderr)
            print("Usage: ai handoff [post|check|check-project|claim|complete]", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "memory":
        if len(sys.argv) < 3 or sys.argv[2] != "watch":
            print("Usage: ai memory watch", file=sys.stderr)
            sys.exit(1)
        from .memory import memory_watch

        sys.exit(memory_watch())

    if len(sys.argv) > 1 and sys.argv[1] == "quota":
        if len(sys.argv) < 3:
            print("Usage: ai quota [watch|status|history|scrape|statusline-part|record|sync]", file=sys.stderr)
            sys.exit(1)
        subcmd = sys.argv[2]
        if subcmd == "watch":
            from .quota import quota_watch

            sys.exit(quota_watch())
        elif subcmd == "status":
            from .quota import quota_status

            sys.exit(quota_status())
        elif subcmd == "history":
            from .quota import quota_history

            sys.exit(quota_history())
        elif subcmd == "scrape":
            from .quota import quota_scrape

            sys.exit(quota_scrape())
        elif subcmd == "statusline-part":
            from .quota import quota_statusline_part

            sys.exit(quota_statusline_part())
        elif subcmd == "sync":
            from .quota import quota_sync_from_remote

            sys.exit(quota_sync_from_remote())
        elif subcmd == "record":
            if len(sys.argv) < 7:
                print("Usage: ai quota record SESSION_ID MACHINE_ID MODEL TOTAL_TOKENS [COST_USD]", file=sys.stderr)
                sys.exit(1)
            from .quota import quota_record

            _session_id = sys.argv[3]
            _machine_id = sys.argv[4]
            _model = sys.argv[5]
            _total_tokens = int(sys.argv[6])
            _cost_usd = float(sys.argv[7]) if len(sys.argv) > 7 else None
            sys.exit(quota_record(_session_id, _machine_id, _model, _total_tokens, _cost_usd))
        else:
            print(f"Unknown quota subcommand: {subcmd}", file=sys.stderr)
            print("Usage: ai quota [watch|status|history|scrape|statusline-part|record|sync]", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "telemetry":
        if len(sys.argv) < 3 or sys.argv[2] != "writer":
            print("Usage: ai telemetry writer", file=sys.stderr)
            sys.exit(1)
        from .telemetry import telemetry_writer

        sys.exit(telemetry_writer())

    if len(sys.argv) > 1 and sys.argv[1] == "gemini":
        from .gemini import gemini_cli

        gemini_cli(sys.argv[2:])
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "spend":
        if len(sys.argv) < 3 or sys.argv[2] != "gemini":
            print("Usage: ai spend gemini", file=sys.stderr)
            sys.exit(1)
        from .spend import cmd_spend_gemini

        sys.exit(cmd_spend_gemini(load_config()))

    if len(sys.argv) > 1 and sys.argv[1] == "cc-usage":
        from .cc_usage import scan_and_push, get_cursor_summary

        subcmd = sys.argv[2] if len(sys.argv) > 2 else None
        if subcmd == "push":
            dry_run = "--dry-run" in sys.argv or "-d" in sys.argv
            result = scan_and_push(config=config, dry_run=dry_run)
            if result.error:
                print(f"Error: {result.error}", file=sys.stderr)
                sys.exit(1)
            if dry_run:
                print(f"Dry run: {result.new_events} new events across {result.scanned_sessions} sessions (not pushed)")
            else:
                print(
                    f"Pushed {result.inserted} new events, {result.skipped} skipped "
                    f"({result.scanned_sessions} sessions scanned)"
                )
            sys.exit(0)
        elif subcmd == "status":
            summary = get_cursor_summary()
            print(f"Sessions tracked: {summary['sessions_tracked']}")
            print(f"Last push:        {summary['last_push'] or 'never'}")
            sys.exit(0)
        else:
            print("Usage: ai cc-usage <push [-d/--dry-run] | status>", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "copier-update":
        if os.environ.get("AI_CLI_HOST") != "mac":
            print("Error: copier-update is Mac only", file=sys.stderr)
            sys.exit(1)

        from .copier_update import run_copier_update

        dry_run = "--dry-run" in sys.argv or "-d" in sys.argv
        project_filter = None
        for _pflag in ("--project", "-p"):
            if _pflag in sys.argv:
                idx = sys.argv.index(_pflag)
                if idx + 1 < len(sys.argv):
                    project_filter = sys.argv[idx + 1]
                break
        sys.exit(run_copier_update(dry_run=dry_run, project_filter=project_filter))

    if len(sys.argv) > 1 and sys.argv[1] == "layout":
        from .layout import run_layout_command

        sys.exit(run_layout_command(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "color":
        # ai color <palette_name_or_hex> — reassign iTerm2 color for the current session
        if len(sys.argv) < 3:
            print("Usage: ai color <palette-color-name-or-hex>", file=sys.stderr)
            sys.exit(1)
        _color_arg = sys.argv[2]
        _ai_name_env = os.environ.get("AI_TMUX_SESSION", "")
        if not _ai_name_env:
            print("ai color: not inside an ai session (AI_TMUX_SESSION not set)", file=sys.stderr)
            sys.exit(1)
        # Resolve color arg: named palette entry or raw hex
        _iterm2_cfg_c = _load_iterm2_config()
        _palette_c = dict(_iterm2_palette(_iterm2_cfg_c))
        if _color_arg.startswith("#"):
            _new_hex = _color_arg
        elif _color_arg in _palette_c:
            _new_hex = f"#{_palette_c[_color_arg]}"
        else:
            print(f"ai color: unknown color '{_color_arg}'. Use a palette name or #RRGGBB.", file=sys.stderr)
            sys.exit(1)
        # Determine engine from session name convention
        _engine_c = "g" if _ai_name_env.startswith("g-") else "c"
        _session_type_c = _iterm2_session_type(_engine_c)
        try:
            from . import icon_generator as _ig_c

            _icon_color_c = _resolve_iterm2_config(_iterm2_cfg_c, _ai_name_env).get("icon_color")
            _ig_c.cleanup_session_files(_ai_name_env)
            _icon_path_c = _ig_c.generate_session_icon(_ai_name_env, _new_hex, _session_type_c, _icon_color_c)
            _ig_c.generate_dynamic_profile(_ai_name_env, _new_hex, _session_type_c, _icon_path_c)
        except Exception as e:
            print(f"ai color: icon generation failed: {e}", file=sys.stderr)
        _color_no_hash_c = _new_hex.lstrip("#")
        _profile_name_c = f"ai-cli:{_ai_name_env}"
        sys.stdout.write(f"\033]1337;SetProfile={_profile_name_c}\007")
        sys.stdout.write(f"\033]1337;SetColors=tab={_color_no_hash_c}\007")
        sys.stdout.flush()
        print(f"Color updated to {_new_hex}")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "tunnel":
        if len(sys.argv) < 3:
            print(
                "Usage: ai tunnel [start <local-port> [remote-port] [-L|--forward] | stop <port> | status]",
                file=sys.stderr,
            )
            sys.exit(1)
        tn_action = sys.argv[2]
        if tn_action == "start":
            if len(sys.argv) < 4:
                print("Usage: ai tunnel start <local-port> [remote-port] [-L|--forward]", file=sys.stderr)
                sys.exit(1)
            local_port = int(sys.argv[3])
            args_rest = sys.argv[4:]
            forward = "--forward" in args_rest or "-L" in args_rest
            non_flag = [a for a in args_rest if not a.startswith("--")]
            remote_port = int(non_flag[0]) if non_flag else local_port
            _cmd_tunnel_start(local_port, remote_port, forward=forward, config=config)
            sys.exit(0)
        elif tn_action == "stop":
            if len(sys.argv) < 4:
                print("Usage: ai tunnel stop <port>", file=sys.stderr)
                sys.exit(1)
            _cmd_tunnel_stop(int(sys.argv[3]))
            sys.exit(0)
        elif tn_action == "status":
            _cmd_tunnel_status()
            sys.exit(0)
        else:
            print(f"Unknown tunnel action: {tn_action}. Use start, stop, or status.", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "signal-watch":
        if len(sys.argv) < 3:
            print("Usage: ai signal-watch [start <project> <session> | stop <session> | status]", file=sys.stderr)
            sys.exit(1)
        sw_action = sys.argv[2]
        if sw_action == "start":
            if len(sys.argv) < 5:
                print("Usage: ai signal-watch start <project> <session>", file=sys.stderr)
                sys.exit(1)
            _cmd_signal_watch_start(sys.argv[3], sys.argv[4])
            sys.exit(0)
        elif sw_action == "stop":
            if len(sys.argv) < 4:
                print("Usage: ai signal-watch stop <session>", file=sys.stderr)
                sys.exit(1)
            _cmd_signal_watch_stop(sys.argv[3])
            sys.exit(0)
        elif sw_action == "status":
            _cmd_signal_watch_status()
            sys.exit(0)
        else:
            print(f"Unknown signal-watch action: {sw_action}. Use start, stop, or status.", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "cdp":
        if len(sys.argv) < 3:
            print(
                "Usage: ai cdp [start [-p N] [-I] [-t] [-L] | stop [-p N] [-t] | status]",
                file=sys.stderr,
            )
            sys.exit(1)
        cdp_action = sys.argv[2]
        _cdp_default_port = config.get("cdp", {}).get("port", 9222)
        if cdp_action == "start":
            _cdp_parser = argparse.ArgumentParser(prog="ai cdp start")
            _cdp_parser.add_argument("-p", "--port", type=int, default=_cdp_default_port)
            _cdp_parser.add_argument("-I", "--no-incognito", action="store_true")
            _cdp_parser.add_argument("-t", "--tunnel", action="store_true", help="start SSH tunnel alongside Chrome")
            _cdp_parser.add_argument(
                "-L", "--forward", action="store_true", help="use forward tunnel (default: reverse)"
            )
            _cdp_args = _cdp_parser.parse_args(sys.argv[3:])
            _cmd_cdp_start(
                _cdp_args.port, not _cdp_args.no_incognito, config, tunnel=_cdp_args.tunnel, forward=_cdp_args.forward
            )
            sys.exit(0)
        elif cdp_action == "stop":
            _cdp_parser = argparse.ArgumentParser(prog="ai cdp stop")
            _cdp_parser.add_argument("-p", "--port", type=int, default=_cdp_default_port)
            _cdp_parser.add_argument(
                "-t", "--tunnel", action="store_true", help="also stop the SSH tunnel for this port"
            )
            _cdp_args = _cdp_parser.parse_args(sys.argv[3:])
            _cmd_cdp_stop(_cdp_args.port, tunnel=_cdp_args.tunnel)
            sys.exit(0)
        elif cdp_action == "status":
            _cmd_cdp_status()
            sys.exit(0)
        else:
            print(f"Unknown cdp action: {cdp_action}. Use start, stop, or status.", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "vpn-watch":
        from .vpn_watch import run_vpn_watch

        run_vpn_watch(config)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "ps":
        from .process_hygiene import cmd_ps

        _ps_exit = cmd_ps(sys.argv[2:], config)
        sys.exit(_ps_exit)

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        if len(sys.argv) == 2:
            print(
                "Usage: ai sync [push|pull|conflicts|watch|cleanup|repair-worktree] [-m|--memories-only] [-n|--dry-run] [-v|--verbose] [-f|--force]"
            )
            sys.exit(1)
        from .sync import (
            sync_push,
            sync_pull,
            sync_conflicts,
            sync_watch,
            repair_worktree_cc_dir,
            clean_worktree_cc_dirs,
            _cc_projects_dir,
            get_local_prefix,
        )

        action = sys.argv[2]
        flags = sys.argv[3:]
        if action == "push":
            sys.exit(sync_push(flags))
        elif action == "pull":
            sys.exit(sync_pull(flags))
        elif action == "conflicts":
            sys.exit(sync_conflicts(flags))
        elif action == "watch":
            sys.exit(sync_watch(flags))
        elif action == "repair-worktree":
            # Usage: ai sync repair-worktree <project> <worktree> [-n|--dry-run] [-v|--verbose]
            positional = [a for a in flags if not a.startswith("-")]
            if len(positional) < 2:
                print(
                    "Usage: ai sync repair-worktree <project> <worktree> [-n|--dry-run] [-v|--verbose]\n"
                    "Example: ai sync repair-worktree job-pilot job-1\n"
                    "Copies all conversations from the main project CC dir into the worktree CC dir\n"
                    "so they are accessible from the worktree session.",
                    file=sys.stderr,
                )
                sys.exit(1)
            project_name = positional[0]
            wt_name = positional[1]
            dry_run = "-n" in flags or "--dry-run" in flags
            verbose = "-v" in flags or "--verbose" in flags
            copied = repair_worktree_cc_dir(
                project_name=project_name,
                wt_name=wt_name,
                cc_projects_dir=_cc_projects_dir(),
                local_prefix=get_local_prefix(),
                dry_run=dry_run,
                verbose=verbose,
            )
            sys.exit(0 if copied >= 0 else 1)
        elif action == "cleanup":
            # Usage: ai sync cleanup [-n|--dry-run] [-v|--verbose]
            # Removes stale duplicate JSONL copies and orphan lock dirs from worktree CC dirs.
            dry_run = "-n" in flags or "--dry-run" in flags
            verbose = "-v" in flags or "--verbose" in flags
            removed_jsonl, removed_lock = clean_worktree_cc_dirs(
                _cc_projects_dir(),
                get_local_prefix(),
                dry_run=dry_run,
                verbose=verbose,
            )
            verb = "Would remove" if dry_run else "Removed"
            print(f"{verb}: {removed_jsonl} stale JSONL copies, {removed_lock} orphan lock dirs")
            sys.exit(0)
        else:
            print(
                f"Unknown sync action: {action}. Use push, pull, conflicts, watch, cleanup, or repair-worktree.",
                file=sys.stderr,
            )
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "reconnect":
        # List remote tmux sessions and print reconnect commands.
        # Usage: ai reconnect [session_numbers...]
        # Examples: ai reconnect (all), ai reconnect 1 3 (specific)
        remote_cfg = config.get("remote", {})
        host = remote_cfg.get("host", "")
        user = remote_cfg.get("user", "ubuntu")
        if not host:
            print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
            sys.exit(1)

        requested = [int(x) for x in sys.argv[2:] if x.isdigit()] if len(sys.argv) > 2 else None

        # Find tmux sessions matching remote pattern on the server
        probe = subprocess.run(
            ["ssh", f"{user}@{host}", "tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            print("Error: could not list remote tmux sessions", file=sys.stderr)
            sys.exit(1)

        remote_sessions = [s.strip() for s in probe.stdout.splitlines() if s.strip().startswith("c-r-")]
        if not remote_sessions:
            print("No remote CC sessions found on server.")
            sys.exit(0)

        # Filter to requested sessions if specified
        if requested:
            remote_sessions = [s for s in remote_sessions if any(s.endswith(f"-{n}") for n in requested)]

        if not remote_sessions:
            print(f"No matching remote sessions for: {requested}")
            sys.exit(0)

        aliases = get_project_aliases()

        # Load active transport state files for annotation
        _state_dir = get_xdg_state_home()
        _transport_by_session: dict[str, str] = {}
        for _tf in _state_dir.glob("transport-*.json"):
            try:
                _td = json.loads(_tf.read_text())
                _transport_by_session[_td.get("session", "")] = _td.get("transport", "")
            except Exception:
                pass

        print(f"Found {len(remote_sessions)} remote session(s). Run each in a separate terminal:\n")
        for session_name in sorted(remote_sessions):
            parts = session_name.split("-")
            if len(parts) >= 4:
                num = parts[-1]
                proj_prefix = "-".join(parts[2:-1])
            else:
                continue
            project_name = aliases.get(proj_prefix, proj_prefix)
            _transport_tag = ""
            _transport = _transport_by_session.get(session_name, "")
            if _transport:
                _transport_tag = f"  [{_transport} connected]"
            if project_name == proj_prefix:
                print(f"  ai c {num} -R{_transport_tag}")
            else:
                print(f"  ai c {num} -R -p {proj_prefix}{_transport_tag}")
        print()
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] in ("update", "deploy"):
        force_reinstall = "--force" in sys.argv or "-f" in sys.argv
        project_path = _find_aicli_project_path(config)
        if project_path is None:
            print(
                "Error: could not locate ai-cli-utils source. Set [deploy] project_path in config.",
                file=sys.stderr,
            )
            sys.exit(1)
        pyproject = project_path / "pyproject.toml"
        if not pyproject.exists():
            print(
                f"Error: pyproject.toml not found at {project_path}. Set [deploy] project_path in config.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Restore pyproject.toml before pull — it may be dirty from an interrupted previous update
        subprocess.run(["git", "checkout", "--", "pyproject.toml"], cwd=project_path, check=False)
        print("Pulling latest from origin...")
        subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=project_path, check=False)
        # Abort if pull left unresolved conflict markers — installing with conflicts produces a broken package
        src_dir = project_path / "src"
        conflict_files = []
        if src_dir.is_dir():
            for py_file in src_dir.rglob("*.py"):
                try:
                    text = py_file.read_text(errors="replace")
                    if any(ln.startswith("<<<<<<< ") or ln.startswith(">>>>>>> ") for ln in text.splitlines()):
                        conflict_files.append(py_file.relative_to(project_path))
                except OSError:
                    pass
        if conflict_files:
            print(
                "Error: unresolved git conflict markers found — resolve before installing:",
                file=sys.stderr,
            )
            for f in conflict_files:
                print(f"  {f}", file=sys.stderr)
            sys.exit(1)
        # Read version after pull so the bump applies to the current remote state
        original = pyproject.read_text()
        m = re.search(r'^(version\s*=\s*")([^"]+)(")', original, re.MULTILINE)
        if not m:
            print("Error: could not find version in pyproject.toml", file=sys.stderr)
            sys.exit(1)
        base = re.sub(r"\.post\d+$", "", m.group(2))
        new_version = f"{base}.post{int(time.strftime('%Y%m%d%H%M%S'))}"
        print(f"Updating {m.group(2)} → {new_version}")
        uv_bin = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        exit_code = 0
        try:
            pyproject.write_text(original[: m.start(2)] + new_version + original[m.end(2) :])
            uv_cmd = [uv_bin, "tool", "install", str(project_path), "--force"]
            if force_reinstall:
                uv_cmd.append("--reinstall")
            result = subprocess.run(uv_cmd, cwd=project_path)
            exit_code = result.returncode
        finally:
            pyproject.write_text(original)
        if exit_code == 0:
            # Install into any configured extra venvs (e.g. tool venvs that depend on ai-cli-utils)
            extra_venvs = config.get("update", {}).get("extra_venvs", [])
            for venv_path_str in extra_venvs:
                venv_path = Path(venv_path_str).expanduser()
                if venv_path.exists():
                    pip_cmd = [uv_bin, "pip", "install", str(project_path)]
                    if force_reinstall:
                        pip_cmd.append("--force-reinstall")
                    subprocess.run(
                        pip_cmd,
                        env={**os.environ, "VIRTUAL_ENV": str(venv_path)},
                        check=False,
                    )
            # Clear pycache
            subprocess.run(
                ["find", str(project_path), "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
                check=False,
            )
            # Record HEAD hash so session start can detect staleness
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_path, capture_output=True, text=True)
            if head.returncode == 0:
                stamp_file = get_xdg_state_home() / "last_update_commit.txt"
                stamp_file.parent.mkdir(parents=True, exist_ok=True)
                stamp_file.write_text(head.stdout.strip())
            # Deploy bundled CC config files to ~/.claude/ — write as plain files so any
            # pre-existing symlinks are replaced. These files are owned by ai-cli-utils and
            # should not be managed by ai sync or tracked in any project git repo.
            _deploy_cc_config_files(project_path)
        sys.exit(exit_code)

    if len(sys.argv) > 1 and sys.argv[1] == "attach":
        if len(sys.argv) < 3:
            print("Usage: ai attach <session-name>", file=sys.stderr)
            sys.exit(1)
        session_name = sys.argv[2]
        check = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True)
        if check.returncode != 0:
            print(f"No tmux session named '{session_name}'", file=sys.stderr)
            sys.exit(1)
        os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])

    if len(sys.argv) > 1 and sys.argv[1] == "ls":
        show_all = "--all" in sys.argv or "-a" in sys.argv

        res = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print("No tmux sessions found (is tmux running?)", file=sys.stderr)
            sys.exit(0)

        now = int(time.time())
        sessions = []
        for line in res.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split()
            name = parts[0]
            try:
                activity = int(parts[1]) if len(parts) > 1 else 0
            except ValueError:
                activity = 0
            if not show_all and not _AI_SESSION_RE.match(name):
                continue
            sessions.append((name, activity))

        if not sessions:
            msg = (
                "No tmux sessions found."
                if show_all
                else "No ai-cli sessions found. Use --all to show all tmux sessions."
            )
            print(msg)
            sys.exit(0)

        sessions.sort(key=lambda x: x[1], reverse=True)

        def _human_age(ts: int) -> str:
            delta = now - ts
            if delta < 60:
                return f"{delta}s"
            if delta < 3600:
                return f"{delta // 60}m"
            if delta < 86400:
                return f"{delta // 3600}h"
            return f"{delta // 86400}d"

        def _project_from_session(name: str) -> str:
            """Extract project prefix from session name: c-myproject-1 → myproject, c-r-myproject-1 → myproject."""
            parts = name.split("-")
            # Format: {c|g}[-r]-{project}-{index}
            if len(parts) >= 3 and parts[0] in ("c", "g"):
                start = 2 if parts[1] == "r" else 1
                # project is everything between start and last segment
                return "-".join(parts[start:-1]) if len(parts) > start + 1 else parts[start]
            return name

        fzf = shutil.which("fzf")
        if fzf is None:
            # Try to install fzf
            apt = shutil.which("apt")
            if apt:
                print("fzf not found — installing with apt...")
                subprocess.run(["apt", "install", "-y", "fzf"], check=False)
                fzf = shutil.which("fzf")

        if fzf:
            lines = [f"{name}\t{_project_from_session(name)}\t{_human_age(activity)}" for name, activity in sessions]
            result = subprocess.run(
                [
                    fzf,
                    "--ansi",
                    "--reverse",
                    "--prompt=session> ",
                    "--delimiter=\t",
                    "--with-nth=1,3",
                    "--preview-window=hidden",
                ],
                input="\n".join(lines),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not result.stdout.strip():
                sys.exit(0)
            selected = result.stdout.strip().split("\t")[0]
            os.execvp("tmux", ["tmux", "attach-session", "-t", selected])
        else:
            # Plain list fallback
            for i, (name, activity) in enumerate(sessions, 1):
                project = _project_from_session(name)
                print(f"  {i}. {name}  ({project})  {_human_age(activity)} ago")
            print("\nTo attach: ai attach <name>")
            sys.exit(0)

    trigger_background_update()
    _auto_update_if_stale(config)
    _ensure_nats_tunnel(config)

    parser = argparse.ArgumentParser(description="Unified AI CLI for Claude and Gemini")
    parser.add_argument("engine", choices=["c", "g"], help="c for Claude, g for Gemini")
    parser.add_argument("name", nargs="?", default="", help="Session name or index")
    parser.add_argument("-r", "--resume", action="store_true", help="Resume an existing session")
    parser.add_argument("-o", "--once", action="store_true", help="Run once without tmux auto-resume loop")
    parser.add_argument("-b", "--bare", action="store_true", help="Run bare tool without tmux at all")
    parser.add_argument("-n", "--notify", action="store_true", help="Fire system notifications on task completion")
    parser.add_argument("-s", "--sandbox", action="store_true", help="Enable sandboxing (default: off)")
    # parser.add_argument("-S", "--no-sandbox", action="store_true", help="Explicitly disable sandboxing")
    parser.add_argument("-W", "--no-worktree", action="store_true", help="Disable git worktree isolation")
    parser.add_argument(
        "-R", "--remote", action="store_true", help="Run session on remote server (configured in [remote])"
    )
    parser.add_argument(
        "-p",
        "--project",
        default="",
        help="Project to open on remote server (directory name, e.g. 'myproject', 'webapp')",
    )
    parser.add_argument("--is-remote", action="store_true", help=argparse.SUPPRESS)  # set by local machine when SSHing
    parser.add_argument("--project-prefix", default="", help=argparse.SUPPRESS)  # override auto-detected project prefix

    args, unknown = parser.parse_known_args()
    # Auto-promote to remote mode when running directly on a non-Mac host so
    # the c-r- / g-r- prefix is applied even without an explicit --is-remote flag.
    args.is_remote = _resolve_is_remote(args.is_remote)
    engine = args.engine
    if args.project_prefix:
        project_prefix = args.project_prefix
    elif args.project and not args.remote:
        # Local -p flag: derive prefix from the target project, not cwd
        _lp_aliases = get_project_aliases()
        _lp_name = _lp_aliases.get(args.project, args.project)
        project_prefix = _get_project_prefix_by_name(_lp_name)
    else:
        project_prefix = get_project_prefix()
    engine_short = "c" if engine == "c" else "g"
    remote_seg = "-r" if args.is_remote else ""
    prefix = f"{engine_short}{remote_seg}-{project_prefix}-"

    # whitelist = config.get("gemini", {}).get("sandbox_whitelist", ["sw"])
    # use_sandbox = False if args.no_sandbox else (True if args.sandbox else project_prefix not in whitelist)
    use_sandbox = args.sandbox

    sandbox_flag = "-s" if use_sandbox else "--no-sandbox"
    gemini_cmd = config.get("gemini", {}).get("command", "gemini")

    name = args.name
    if not name and unknown:
        name = unknown[0]

    if args.remote:
        remote_cfg = config.get("remote", {})
        host = remote_cfg.get("host", "")
        if not host:
            print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
            sys.exit(1)
        user = remote_cfg.get("user", "ubuntu")
        port = str(remote_cfg.get("port", 22))
        id_file = remote_cfg.get("identity_file", "")
        transport = remote_cfg.get("transport", "mosh")
        aliases = get_project_aliases()
        raw_project = args.project or get_current_project_name()
        if args.project and ("/" in args.project or "\\" in args.project):
            print("Error: --project name must not contain path separators", file=sys.stderr)
            sys.exit(1)
        remote_project = aliases.get(raw_project, raw_project)
        # When -p is provided, derive prefix from the target project's task_prefix
        if args.project:
            remote_prefix = _get_project_prefix_by_name(remote_project)
        else:
            remote_prefix = project_prefix
        # Prepend ~/.local/bin to PATH so `ai` is found on the remote side even
        # when the shell is a non-interactive login shell (zsh -l -c) that does
        # not source ~/.zshrc where the uv env PATH setup typically lives.
        remote_cmd = f'export PATH="$HOME/.local/bin:$PATH"; ai {engine} --is-remote --project-prefix {remote_prefix} --project {shlex.quote(remote_project)}'
        if args.resume:
            remote_cmd += " --resume"
        if name:
            remote_cmd += f" {shlex.quote(name)}"
        # Emit iTerm2 profile/color before mosh/ssh takes over the pane.
        # mosh blocks all \033]1337; sequences from the remote side, so this
        # is the only opportunity to set the profile and tab color.
        _r_engine_short = "c" if engine == "c" else "g"
        _r_ai_name = f"{_r_engine_short}-r-{remote_prefix}-{name or '1'}"
        _iterm2_remote_slot = _assign_iterm2_color_slot(_r_ai_name, engine)
        _emit_iterm2_profile_setup(_r_ai_name, engine, _r_ai_name, slot=_iterm2_remote_slot)

        _cleanup_cmd = ["ai", "internal", "cleanup-session-files", _r_ai_name]
        # vpn_host: direct-IP host used for SSH when VPN is active (bypasses Tailscale/WireGuard
        # which becomes unreachable when a split-tunneling VPN like Mullvad takes over routing).
        # Falls back to host when not set.
        vpn_host = remote_cfg.get("vpn_host", "") or host
        ssh_args = ["ssh", "-t", "-p", port]
        if id_file:
            ssh_args += ["-i", os.path.expanduser(id_file)]
        ssh_args.append(f"{user}@{vpn_host}")
        ssh_args.append(f"zsh -l -c {shlex.quote(remote_cmd)}")

        # Build mosh_args unconditionally — needed for both initial connection
        # and for reconnecting after a VPN drop while on SSH.
        # mosh always uses the primary host (Tailscale/LAN) since it only runs without VPN.
        # ConnectTimeout=10 on the SSH phase ensures mosh fails fast (error + exit) instead
        # of hanging silently for ~2 minutes when the host is unreachable (e.g. Tailscale down).
        mosh_args = ["mosh"]
        _mosh_ssh = "ssh -o ConnectTimeout=10"
        if port != "22":
            _mosh_ssh += f" -p {port}"
        if id_file:
            _mosh_ssh += f" -i {shlex.quote(os.path.expanduser(id_file))}"
        mosh_args += ["--ssh", _mosh_ssh]
        mosh_args.append(f"{user}@{host}")
        mosh_args += ["--", "zsh", "-l", "-c", remote_cmd]

        if transport == "mosh":
            _ensure_vpn_watcher(config)
            import asyncio as _asyncio

            try:
                _asyncio.run(
                    _run_transport_loop(ssh_args, mosh_args, _cleanup_cmd, _r_ai_name, config, tailscale_host=host)
                )
            finally:
                _maybe_stop_vpn_watcher()
            sys.exit(0)
        else:
            # Pure SSH transport — no VPN switching.
            os.execvp("zsh", ["zsh", "-c", f"{shlex.join(ssh_args)}; {shlex.join(_cleanup_cmd)} 2>/dev/null"])

    # When running as the remote side of an --remote session, cd into the project directory
    # before creating the worktree so git commands work correctly.
    if args.is_remote:
        aliases = get_project_aliases()
        raw_project = args.project or config.get("remote", {}).get("project") or _get_main_project_name()
        if raw_project:
            project_name = aliases.get(raw_project, raw_project)
            project_dir = _find_project_dir(project_name)
            if project_dir.exists():
                os.chdir(project_dir)
    elif args.project:
        # Local session with explicit -p PROJECT: cd to the project directory so that
        # git worktrees and Gemini chats directories resolve relative to the correct root.
        # Mirrors the is_remote path above.
        aliases = get_project_aliases()
        _local_project = aliases.get(args.project, args.project)
        _local_project_dir = _find_project_dir(_local_project)
        if _local_project_dir.exists():
            os.chdir(_local_project_dir)

    if args.bare:
        if engine == "c":
            perms = [] if os.getuid() == 0 else ["--dangerously-skip-permissions"]
            os.execvp("claude", ["claude"] + perms + unknown)
        else:
            _gcmd = shlex.split(gemini_cmd)
            os.execvp(_gcmd[0], _gcmd + ["-y", "-s" if use_sandbox else "--no-sandbox"] + unknown)

    if args.resume:
        session = resolve_session(prefix, name)
        if not session:
            print(f"No matching session found for '{prefix}{name or '*'}'")
            sys.exit(1)
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])

    # Registry validation: ensure all projects are registered before launching session
    if not validate_registry_completeness(interactive=sys.stdin.isatty()):
        sys.exit(1)

    cleanup_stale_sessions(config)
    current_project_name = get_current_project_name()
    session_id, ai_name = build_session_name(engine, project_prefix, name, config, is_remote=args.is_remote)

    # Worktree setup
    worktree_path = None
    if config.get("worktree", {}).get("enabled", True) and not args.no_worktree:
        worktree_path = create_worktree(ai_name)
        if worktree_path:
            # Sync worktree with any changes that landed on main from other sessions
            subprocess.run(["git", "pull", "--rebase", "--autostash"], capture_output=True, cwd=worktree_path)

    d = get_session_map(engine)
    uuid = d.get(ai_name)
    # For Gemini, always check the chats directory for the latest session — the
    # session map may be stale if the user exited and restarted directly via gemini CLI.
    if engine == "g":
        latest = _find_latest_gemini_uuid(ai_name)
        if latest and latest != uuid:
            uuid = latest
            d[ai_name] = uuid
            save_session_map(d, engine)

    # Propagate iTerm2 env vars into the tmux session — tmux doesn't inherit these,
    # so _iterm2_fleet_setup inside the bash script would silently no-op without them.
    _iterm_env_flags: list[str] = []
    for _var in ("ITERM_SESSION_ID", "LC_TERMINAL", "TERM_PROGRAM"):
        if _val := os.environ.get(_var):
            _iterm_env_flags += ["-e", f"{_var}={_val}"]

    if args.once:
        cd_pref = f"cd {worktree_path} && " if worktree_path else ""
        if engine == "c":
            perms = "" if os.getuid() == 0 else "--dangerously-skip-permissions"
            os.execvp(
                "tmux",
                ["tmux", "new-session", "-s", session_id]
                + _iterm_env_flags
                + ["--", "zsh", "-c", f"{cd_pref}claude {perms} --name {ai_name}".strip()],
            )
        else:
            if uuid:
                os.execvp(
                    "tmux",
                    ["tmux", "new-session", "-s", session_id]
                    + _iterm_env_flags
                    + ["--", "zsh", "-c", f"{cd_pref}{gemini_cmd} -y {sandbox_flag} -r {uuid}"],
                )
            else:
                os.execvp(
                    "tmux",
                    ["tmux", "new-session", "-s", session_id]
                    + _iterm_env_flags
                    + ["--", "zsh", "-c", f"{cd_pref}{gemini_cmd} -y {sandbox_flag} -i '/resume load {ai_name}'"],
                )

    # Assign iTerm2 color slot before generating the script so both the pre-launch
    # emission and the embedded bash variables use the same slot.
    _iterm2_cfg = _load_iterm2_config()
    _iterm2_slot = _assign_iterm2_color_slot(ai_name, engine, project_name=current_project_name)

    _config_reload_idle_secs = int(config.get("session", {}).get("config_reload_idle_secs", 90))
    script = get_engine_script(
        engine,
        ai_name,
        session_id,
        prefix,
        project_prefix,
        uuid,
        use_sandbox,
        str(worktree_path) if worktree_path else None,
        notify=args.notify,
        is_remote=args.is_remote,
        project_name=current_project_name,
        iterm2_slot=_iterm2_slot,
        iterm2_cfg=_iterm2_cfg,
        config_reload_idle_secs=_config_reload_idle_secs,
        gemini_cmd=gemini_cmd,
    )
    # Emit iTerm2 profile/color/title now, before tmux takes over the pane.
    # This fires in the current shell (no DCS wrapping needed) so it works
    # for new tabs, split panes, and re-attaches alike.
    _emit_iterm2_profile_setup(ai_name, engine, session_id, slot=_iterm2_slot, project_name=current_project_name)

    # Check if session already exists (e.g., re-attaching after disconnect)
    existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True)
    explicit_sandbox = args.sandbox
    if existing.returncode == 0 and explicit_sandbox:
        # Explicit sandbox flag — kill old session so it recreates with new settings
        subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True)
        existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True)
    if existing.returncode == 0:
        # Session exists — configure for iTerm2, then attach (detach stale clients)
        _configure_tmux_for_iterm2(session_id)
        os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])
    else:
        # New session: create detached so tmux options can be set before attaching.
        # tmux always allocates a PTY for the pane regardless of client attachment,
        # so Claude Code gets a proper PTY once we attach immediately after.
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_id] + _iterm_env_flags + ["--", "zsh", "-c", script],
            capture_output=True,
        )
        _configure_tmux_for_iterm2(session_id)
        os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])


if __name__ == "__main__":  # pragma: no cover
    cli()
