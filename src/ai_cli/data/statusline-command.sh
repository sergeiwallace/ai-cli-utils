#!/usr/bin/env bash
# Claude Code status line — compact layout
# Shows: clock | model | project:branch | tmux session | ctx% | cost | quota | tip

input=$(cat)

# --- Parse all JSON fields in one jq call ---
# model field can be a string or object depending on CC version
mapfile -t _f < <(echo "$input" | jq -r '
  (.model | if type == "object" then .display_name else . end) // "Unknown",
  (.model | if type == "object" then .id else . end) // "unknown",
  (.context_window.used_percentage // ""),
  (.context_window.total_input_tokens // 0),
  (.context_window.total_output_tokens // 0),
  (.context_window.current_usage.cache_creation_input_tokens // 0),
  (.context_window.current_usage.cache_read_input_tokens // 0),
  (.workspace.project_dir // "")
' 2>/dev/null)
model_display="${_f[0]}"
model_id="${_f[1]}"
used_pct="${_f[2]}"
total_in="${_f[3]}"
total_out="${_f[4]}"
cache_write="${_f[5]}"
cache_read="${_f[6]}"
_project_dir_from_json="${_f[7]}"

# --- Model display name (short) ---
model_lower="${model_display,,}"
if [[ "$model_lower" == *"opus"* ]]; then   model_short="Opus"
elif [[ "$model_lower" == *"haiku"* ]]; then model_short="Haiku"
elif [[ "$model_lower" == *"sonnet"* ]]; then model_short="Sonnet"
else model_short="${model_display%% [0-9]*}"; fi

# --- Context window % with color ---
if [ -n "$used_pct" ]; then
  used_int=$(printf "%.0f" "$used_pct")
  if   [ "$used_int" -lt 50 ]; then ctx_part="\033[32m${used_int}%\033[0m"
  elif [ "$used_int" -lt 80 ]; then ctx_part="\033[33m${used_int}%\033[0m"
  else                               ctx_part="\033[31m${used_int}%\033[0m"; fi
else
  ctx_part="\033[2m--%\033[0m"
fi

# --- Session cost ---
if [[ "$model_lower" == *"opus"* ]]; then
  in_rate=15.00; out_rate=75.00; cw_rate=18.75; cr_rate=1.50
elif [[ "$model_lower" == *"haiku"* ]]; then
  in_rate=0.80; out_rate=4.00; cw_rate=1.00; cr_rate=0.08
else
  in_rate=3.00; out_rate=15.00; cw_rate=3.75; cr_rate=0.30
fi
session_cost=$(awk -v ti="$total_in" -v to="$total_out" -v cw="$cache_write" -v cr="$cache_read" \
  -v ir="$in_rate" -v outr="$out_rate" -v wr="$cw_rate" -v rr="$cr_rate" \
  'BEGIN { printf "$%.2f", (ti/1000000)*ir + (to/1000000)*outr + (cw/1000000)*wr + (cr/1000000)*rr }')

# --- Project name ---
_project_dir="${CLAUDE_PROJECT_DIR:-${_project_dir_from_json:-$(pwd)}}"
if [[ "$_project_dir" == *"/.worktrees/"* ]]; then
  project="${_project_dir%%/.worktrees/*}"; project="${project##*/}"
else
  project="${_project_dir##*/}"
fi

# --- Git branch (compact: worktree branches shown as #N) ---
# File-based 5s cache avoids a ~100ms git subprocess on every render cycle.
# Cache format: line 1 = Unix timestamp written, line 2 = branch name.
_bcache="${TMPDIR:-/tmp}/.ai-sl-branch-${UID:-0}"
branch=""
if [ -n "$GIT_BRANCH_CACHE" ]; then
  branch="$GIT_BRANCH_CACHE"
elif [[ -f "$_bcache" ]]; then
  { IFS= read -r _bts && IFS= read -r branch; } < "$_bcache" 2>/dev/null
  [[ "$_bts" =~ ^[0-9]+$ ]] && (( $(date +%s) - _bts < 5 )) || branch=""
fi
if [[ -z "$branch" ]]; then
  branch=$(git -C "$_project_dir" branch --show-current 2>/dev/null)
  branch="${branch:-?}"
  printf '%d\n%s' "$(date +%s)" "$branch" > "$_bcache" 2>/dev/null
fi
if [[ "$branch" =~ ^wt-.*-([0-9]+)$ ]]; then branch_display="#${BASH_REMATCH[1]}"
elif [ "${#branch}" -gt 14 ]; then             branch_display="${branch:0:13}…"
else                                            branch_display="$branch"; fi

# --- Tmux session (single call for both name and session_id) ---
tmux_part=""
_telem_session="unknown"
if [ -n "$TMUX" ]; then
  _tmux_info=$(tmux display-message -p '#{session_name}|#{session_id}' 2>/dev/null)
  tmux_session="${_tmux_info%|*}"
  _telem_session="${_tmux_info#*|}"
  [ -n "$tmux_session" ] && tmux_part="\033[35m${tmux_session}\033[0m"
fi

# --- Rotating tips (every 45 seconds) ---
tips=("/compact to free context" "Shift+Tab accepts edits" "#file adds files to context"
  "/cost for detailed costs" "/memory to view memories" "Esc interrupts Claude"
  "/review for uncommitted changes" "Tab Tab: accept + run" "/model to switch models"
  "@ to reference commits or PRs" "Ctrl+R: search history" "/clear resets context"
  "Paste or drag images to context" "/vim for vim-mode" "--- adds a thinking pause"
  "\\ for multi-line input" "claude -p for non-interactive" "/hooks views automation"
  "? for all shortcuts")
tip_index=$(( $(date +%s) / 45 % ${#tips[@]} ))
tip="\033[2;3m${tips[$tip_index]}\033[0m"

# --- Clock ---
clock=$(date +%H:%M)

# --- Telemetry (rate-limited fire-and-forget) ---
# ai quota record has ~680ms startup overhead. CC calls statusLine on every render
# cycle, so recording every call stacks up dozens of competing background processes.
# Rate-limit to once per 60s — quota data is 10-minute resolution anyway.
# Cache format: single line containing the Unix timestamp of the last record call.
_telem_tokens=$(( total_in + total_out ))
_tcache="${TMPDIR:-/tmp}/.ai-sl-telem-${UID:-0}"
_do_record=1
if [[ -f "$_tcache" ]]; then
  IFS= read -r _tts < "$_tcache" 2>/dev/null
  [[ "$_tts" =~ ^[0-9]+$ ]] && (( $(date +%s) - _tts < 60 )) && _do_record=0
fi
if (( _do_record )); then
  printf '%d' "$(date +%s)" > "$_tcache" 2>/dev/null
  ai quota record "${_telem_session}" "$(hostname)" "${model_id}" "${_telem_tokens}" >/dev/null 2>&1 &
fi

# --- Quota indicator (cached) ---
# ai quota statusline-part has ~1.4s startup overhead (Python + ai_cli import + SQLite).
# CC calls statusLine on every render cycle — during streaming that's many times/sec.
# A blocking 1.4s call causes render cycles to overlap, producing duplicate boxes in
# the scrollback buffer. Cache the output for 30s: quota data changes every 10 minutes,
# so 30s staleness is negligible.
# Cache format: line 1 = Unix timestamp written, line 2 = quota output (may be empty).
_qcache="${TMPDIR:-/tmp}/.ai-sl-quota-${UID:-0}"
quota_part=""
if [[ -f "$_qcache" ]]; then
  { IFS= read -r _qts && IFS= read -r quota_part; } < "$_qcache" 2>/dev/null
  # Guard: if first line is not a valid Unix timestamp, treat cache as stale (old format).
  [[ "$_qts" =~ ^[0-9]+$ ]] && (( $(date +%s) - _qts < 30 )) || quota_part=""
fi
if [[ -z "$quota_part" ]]; then
  quota_part=$(ai quota statusline-part 2>/dev/null)
  # Strip embedded newlines: statusLine contract requires exactly one output line.
  quota_part="${quota_part//$'\n'/ }"
  printf '%d\n%s' "$(date +%s)" "$quota_part" > "$_qcache" 2>/dev/null
fi

# --- Assemble ---
sep="\033[2m│\033[0m"
line="\033[2m${clock}\033[0m ${sep} ${model_short} ${sep} \033[1m${project}\033[0m:\033[36m${branch_display}\033[0m"
[ -n "$tmux_part" ] && line="${line} ${sep} ${tmux_part}"
line="${line} ${sep} ${ctx_part} ${sep} ${session_cost}"
[ -n "$quota_part" ] && line="${line} ${sep} ${quota_part}"
line="${line} ${sep} ${tip}"
# \033[0m resets colors; \033[K erases from cursor to end of line.
# When CC positions the cursor at the start of the status line and writes our output,
# \033[K clears any leftover characters from a previously longer render — preventing
# stale character artifacts from accumulating in the scrollback buffer.
printf '%b\033[0m\033[K\n' "$line"
