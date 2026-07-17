#!/usr/bin/env bash
# Claude Code status line — compact layout
# Shows: clock | model | project:branch ⎇ worktree | [tmux session — only when NOT in a worktree] | ctx% | quota / cache↑% | tip
#
# CC_BILLING_MODE=subscription (default) — show OAuth quota via `ai quota statusline-part`
# CC_BILLING_MODE=api                   — show cache-hit % only. The four cost items
#   ($X.XX session, 24h:, Σ24h:, Σ7d:) were removed 2026-06-08 pending an accuracy
#   fix — the dollar figures were wildly inflated (tracked by roadmap AIH-23).

# --- Single-flight guard (prevents the render fork-bomb) ---
# CC re-invokes the status line on every render (many times/sec while streaming).
# On slow platforms (Windows Git-Bash, where process creation is expensive) a
# render can outlast the interval to the next invocation; CC then orphans the slow
# one and spawns a new one. Without a guard these accumulate without bound → a
# fork-bomb that pegs the CPU (observed 2026-07-02: 300+ stuck statusline procs at
# 100% CPU). This makes a new render exit immediately — reprinting the last line —
# when the previous render for THIS session is still alive. Per-session key so
# concurrent CC sessions never block each other; a dead PID (e.g. a killed backlog)
# falls through and proceeds. Near-zero added cost on the normal (uncontended) path.
#
# Key on CLAUDE_CODE_SESSION_ID — the real per-session env var CC exports (inherited
# by this hook). The old key used CLAUDE_SESSION_ID, which CC never sets (same bug
# SW-873/AIH-67 fixed for the context sentinel below): it fell through to $PPID, and
# on Windows Git-Bash CC spawns the statusline reparented to PID 1, so EVERY session
# collapsed to key "1" — one shared lock + last-line cache across all sessions. Result:
# one session reprinted another's cached line, so concurrent sessions showed an
# identical (often stale) ctx% (e.g. a just-compacted session still showing the other
# session's 65%). $PPID stays only as a last-resort fallback when the env var is unset.
_sl_key="${CLAUDE_CODE_SESSION_ID:-$PPID}"
_sl_lock="${TMPDIR:-/tmp}/.ai-sl-run-${UID:-0}-${_sl_key}"
_sl_last="${TMPDIR:-/tmp}/.ai-sl-last-${UID:-0}-${_sl_key}"
if [ -e "$_sl_lock" ]; then
  _sl_prev=$(cat "$_sl_lock" 2>/dev/null)
  if [ -n "$_sl_prev" ] && kill -0 "$_sl_prev" 2>/dev/null; then
    cat "$_sl_last" 2>/dev/null   # previous render still running — reprint last line, skip this cycle
    exit 0
  fi
fi
echo $$ > "$_sl_lock" 2>/dev/null
trap 'rm -f "$_sl_lock" 2>/dev/null' EXIT

# Windows Git Bash (msys/cygwin): CC doesn't close the stdin pipe, so plain `cat`
# blocks forever → the script hangs → lock file persists with a live PID → every
# subsequent render just replays _sl_last → statusline freezes. Add a 2-second
# timeout on Windows only; macOS/Linux pipe closes immediately so they're unaffected.
if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  input=$(timeout 2 cat 2>/dev/null)
else
  input=$(cat)
fi
input="${input:-{}}"

# --- Parse all JSON fields in one jq call ---
# model field can be a string or object depending on CC version
mapfile -t _f < <(echo "$input" | jq -r '
  (.model | if type == "object" then .display_name else . end) // "Unknown",
  (.model | if type == "object" then .id else . end) // "unknown",
  (.context_window.total_input_tokens // 0),
  (.context_window.total_output_tokens // 0),
  (.workspace.project_dir // ""),
  (.cost.total_cost_usd // 0),
  (.context_window.used_percentage // ""),
  (.context_window.context_window_size // 0),
  (.session_id // ""),
  (.transcript_path // "")
' 2>/dev/null | tr -d '\r')
model_display="${_f[0]}"
model_id="${_f[1]}"
total_in="${_f[2]}"
total_out="${_f[3]}"
_project_dir_from_json="${_f[4]}"
# shellcheck disable=SC2034  # positional unpack; not all fields are consumed
session_cost="${_f[5]}"
used_pct="${_f[6]}"
ctx_size="${_f[7]}"
session_id="${_f[8]}"
transcript_path="${_f[9]}"

# --- Model display name (short) ---
model_lower="${model_display,,}"
if [[ "$model_lower" == *"opus"* ]]; then   model_short="Opus"
elif [[ "$model_lower" == *"haiku"* ]]; then model_short="Haiku"
elif [[ "$model_lower" == *"sonnet"* ]]; then model_short="Sonnet"
else model_short="${model_display%% [0-9]*}"; fi

# --- Context window % with color (dynamic, model-aware) ---
# CC's context_window.used_percentage is correct WHEN it reports the right
# context_window_size. But for 1M-capable models actually running a 1M window, CC
# sometimes still reports context_window_size=200000 (under-report), which inflates
# (and clamps) used_percentage. We self-correct: derive the model's max window from
# its id, detect whether a >200k window is genuinely active, compute the effective
# window, and recompute the % only when CC under-reports. When CC's size matches the
# effective window we trust its number verbatim so it mirrors the native TUI. Shows
# --% when no usage data is available (older CC / non-API surfaces).

# 1. Max window the model is capable of. Among current models only Haiku 4.5
# (and older gens) are 200k — Fable 5, all Opus 4.6/4.7/4.8, and both Sonnet 4.6
# and Sonnet 5 are 1M. List the 1M-capable ids explicitly (safer than defaulting
# 1M, so a future 200k model isn't silently mis-sized). Update when a new 1M model ships.
_model_max=200000
case "$model_id" in
  *opus-4-6* | *opus-4-7* | *opus-4-8* | *sonnet-4-6* | *sonnet-5* | *fable-5* | *mythos-5*) _model_max=1000000 ;;
esac

# 2. Is a >200k window genuinely active this session? Any one signal suffices:
#    explicit [1m] tag, CC-reported 1M size, token count already past 200k, or an
#    API-gateway backend (CC_BILLING_MODE=api → Foundry/Vertex, where 1M-capable
#    models always run the full 1M window). The last signal closes the sub-200k gap
#    (AIH-53): without it, a 1M session under 200k tokens (e.g. right after /compact)
#    fails all other signals and falls back to CC's inflated 200k-based %.
_win_active_1m=0
if [[ "$model_id" == *"[1m]"* ]] \
   || [ "${ctx_size:-0}" = "1000000" ] \
   || [ "${CC_BILLING_MODE:-}" = "api" ] \
   || { [[ "$total_in" =~ ^[0-9]+$ ]] && [ "$total_in" -gt 200000 ]; }; then
  _win_active_1m=1
fi

# 3. Effective window = capability ∧ activation; else a sane CC-reported size; else 200k.
if [ "$_model_max" = "1000000" ] && [ "$_win_active_1m" = "1" ]; then
  effective_window=1000000
elif [[ "${ctx_size:-}" =~ ^[0-9]+$ ]] && [ "$ctx_size" -ge 200000 ]; then
  effective_window="$ctx_size"
else
  effective_window=200000
fi

# 4. Pick the percentage: trust CC when its reported size matches the effective
#    window (its used_percentage then matches the native TUI); recompute from
#    tokens when CC under-reports the window size.
used_int=""
if [ "${ctx_size:-0}" = "$effective_window" ] && [ -n "$used_pct" ]; then
  used_int=$(printf "%.0f" "$used_pct" 2>/dev/null || echo "")
elif [[ "$total_in" =~ ^[0-9]+$ ]] && [ "$total_in" -gt 0 ]; then
  used_int=$(awk -v t="$total_in" -v w="$effective_window" 'BEGIN{printf "%.0f", (t/w)*100}')
elif [ -n "$used_pct" ]; then
  used_int=$(printf "%.0f" "$used_pct" 2>/dev/null || echo "")
fi

# Clamp 0–100.
if [[ "${used_int:-}" =~ ^-?[0-9]+$ ]]; then
  [ "$used_int" -lt 0 ] && used_int=0
  [ "$used_int" -gt 100 ] && used_int=100
fi

# --- AIH-67: Context-high sentinel for /save-state automation ---
# Writes ~/.claude/state/context-high-<session> when context reaches 50%.
# UserPromptSubmit hook (context-high-notice.sh) reads this flag and injects
# a one-time notice to invoke /save-state. The hook writes a "noticed" marker
# so the notice fires once per crossing; when context drops below 50% (e.g.
# after /compact), the noticed marker is cleared so the cycle can restart.
# SW-873 bug-fix: key on the payload .session_id (== the agent's CLAUDE_CODE_SESSION_ID).
# The old key `${CLAUDE_SESSION_ID:-unknown}` used an env var CC never sets (the real one
# is CLAUDE_CODE_SESSION_ID), so every session wrote `context-high-unknown` → concurrent
# sessions collided on one shared flag. Prefer the payload id; fall back to the env id.
_ctx_sess="${session_id:-${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-unknown}}}"
_ctx_state="$HOME/.claude/state"
mkdir -p "$_ctx_state" 2>/dev/null   # ensure the state dir exists before touch/write
_ctx_flag="${_ctx_state}/context-high-${_ctx_sess}"
_ctx_noticed="${_ctx_state}/context-high-noticed-${_ctx_sess}"
if [[ "${used_int:-}" =~ ^[0-9]+$ ]]; then
    if [[ "$used_int" -ge 50 ]]; then
        if [[ ! -f "$_ctx_noticed" ]] && [[ ! -f "$_ctx_flag" ]]; then
            touch "$_ctx_flag" 2>/dev/null
        fi
    else
        [[ -f "$_ctx_noticed" ]] && rm -f "$_ctx_noticed" 2>/dev/null
    fi
fi

# --- SW-873: persist the numeric ctx% so the agent can read its own context state ---
# The agent has no native context-% signal (it guesses — said "nearly full" at 34%).
# Write the corrected/displayed `used_int` (+ tokens/window/cwd/updated_at) to a per-session
# JSON state file plus a cwd-keyed pointer, so the `ctx-pct` helper can resolve THIS
# session's % for the smart save-state compact-gate. Atomic (temp + mv). Skipped when
# there is no real session id (never writes a shared `-unknown` file).
if [[ "${used_int:-}" =~ ^[0-9]+$ ]] && [ -n "${session_id:-}" ]; then
  mkdir -p "$_ctx_state" 2>/dev/null
  _pct_now="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)"
  # Build the state JSON with jq, NOT printf: jq --arg correctly JSON-escapes every string
  # value on all platforms. A raw printf emitted a Windows cwd (C:\Users\...) as illegal JSON
  # escapes (\U, \w, ...), so the file was unparseable by jq in ctx-pct and every field read
  # empty -> "unavailable". --arg passes numbers as strings; `tonumber? // 0` coerces them
  # back to numbers robustly (empty/non-numeric -> 0). No behavioral change on mac/linux.
  _pct_json=$(jq -nc \
    --arg used "${used_int}" --arg tin "${total_in:-0}" --arg tout "${total_out:-0}" \
    --arg win "${effective_window:-0}" --arg model "${model_id}" --arg sess "${_ctx_sess}" \
    --arg cwd "${_project_dir_from_json}" --arg now "${_pct_now}" --arg tpath "${transcript_path:-}" \
    '{used_percentage:($used|tonumber? // 0),total_input_tokens:($tin|tonumber? // 0),total_output_tokens:($tout|tonumber? // 0),effective_window:($win|tonumber? // 0),model_id:$model,session_id:$sess,cwd:$cwd,transcript_path:$tpath,updated_at:$now}' 2>/dev/null)
  _pct_tmp="${_ctx_state}/.context-pct-${_ctx_sess}.tmp.$$"
  if [ -n "$_pct_json" ] && printf '%s' "$_pct_json" > "$_pct_tmp" 2>/dev/null; then
    mv -f "$_pct_tmp" "${_ctx_state}/context-pct-${_ctx_sess}.json" 2>/dev/null
  fi
  # cwd-keyed pointer — resolvable without the session id (belt-and-suspenders for the agent).
  if [ -n "${_project_dir_from_json:-}" ]; then
    _dirkey=$(printf '%s' "$_project_dir_from_json" | cksum | cut -d' ' -f1)
    _pct_dtmp="${_ctx_state}/.context-pct-bydir-${_dirkey}.tmp.$$"
    if printf '%s' "$_pct_json" > "$_pct_dtmp" 2>/dev/null; then
      mv -f "$_pct_dtmp" "${_ctx_state}/context-pct-bydir-${_dirkey}.json" 2>/dev/null
    fi
  fi
fi

if [[ "${used_int:-}" =~ ^[0-9]+$ ]]; then
  if   [ "$used_int" -lt 50 ]; then ctx_part="🪟 \033[32m${used_int}%\033[0m"
  elif [ "$used_int" -lt 80 ]; then ctx_part="🪟 \033[33m${used_int}%\033[0m"
  else                               ctx_part="🪟 \033[31m${used_int}%\033[0m"; fi
else
  ctx_part="🪟 \033[2m--%\033[0m"
fi

# --- Project name + worktree detection ---
# Handles both CC native worktrees (.claude/worktrees/) and generic worktrees (.worktrees/).
_project_dir="${CLAUDE_PROJECT_DIR:-${_project_dir_from_json:-$(pwd)}}"
worktree_name=""
_main_repo_dir=""
if [[ "$_project_dir" == *"/.claude/worktrees/"* ]]; then
  _main_repo_dir="${_project_dir%%/.claude/worktrees/*}"
  project="${_main_repo_dir##*/}"
  worktree_name="${_project_dir##*/.claude/worktrees/}"
elif [[ "$_project_dir" == *"/.worktrees/"* ]]; then
  _main_repo_dir="${_project_dir%%/.worktrees/*}"
  project="${_main_repo_dir##*/}"
  worktree_name="${_project_dir##*/.worktrees/}"
else
  project="${_project_dir##*/}"
fi

# --- Git branch (compact: worktree branches shown as #N) ---
# HEAD-mtime cache: ~1ms stat replaces ~100ms git on every render cycle.
# Cache invalidates immediately when .git/HEAD changes (branch switch, commit, rebase).
# Cache format: line 1 = HEAD file mtime (epoch), line 2 = branch name.
_dir_hash=$(echo "$_project_dir" | cksum | cut -d' ' -f1)
_bcache="${TMPDIR:-/tmp}/.ai-sl-branch-${UID:-0}-${_dir_hash}"
branch=""
# Locate HEAD file: follow gitdir link for worktrees (.git is a file, not a dir)
_head_file="$_project_dir/.git/HEAD"
if [[ -f "$_project_dir/.git" ]]; then
  _gitdir=$(sed 's/^gitdir: //' "$_project_dir/.git" 2>/dev/null)
  [[ "$_gitdir" != /* ]] && _gitdir="$_project_dir/$_gitdir"
  _head_file="$_gitdir/HEAD"
fi
# Cache key = "mtime:firstbyte" — the first byte of HEAD distinguishes 'r' (ref:, on a branch)
# from a hex digit (detached), catching fast checkouts that land within the same mtime second.
_head_mtime=$(stat -c %Y "$_head_file" 2>/dev/null || echo "0")
_head_fb=$(head -c 1 "$_head_file" 2>/dev/null || echo "0")
_head_sig="${_head_mtime}:${_head_fb}"
if [ -n "$GIT_BRANCH_CACHE" ]; then
  branch="$GIT_BRANCH_CACHE"
elif [[ -f "$_bcache" ]]; then
  { IFS= read -r _cached_sig && IFS= read -r branch; } < "$_bcache" 2>/dev/null
  [[ "$_cached_sig" == "$_head_sig" ]] || branch=""
fi
if [[ -z "$branch" ]]; then
  branch=$(git -C "$_project_dir" branch --show-current 2>/dev/null)
  if [[ -z "$branch" ]]; then
    _sha=$(git -C "$_project_dir" rev-parse --short HEAD 2>/dev/null)
    branch="${_sha:+@${_sha}}"
  fi
  branch="${branch:-?}"
  printf '%s\n%s' "$_head_sig" "$branch" > "$_bcache" 2>/dev/null
fi
if [[ "$branch" =~ ^wt-.*-([0-9]+)$ ]]; then branch_display="#${BASH_REMATCH[1]}"
elif [ "${#branch}" -gt 14 ]; then             branch_display="${branch:0:13}…"
else                                            branch_display="$branch"; fi

# --- Primary branch (main working tree branch, only used when in a worktree) ---
primary_branch_display=""
if [[ -n "$_main_repo_dir" ]]; then
  _pb_hash=$(echo "$_main_repo_dir" | cksum | cut -d' ' -f1)
  _pb_cache="${TMPDIR:-/tmp}/.ai-sl-pbranch-${UID:-0}-${_pb_hash}"
  _main_head="$_main_repo_dir/.git/HEAD"
  _pb_mtime=$(stat -c %Y "$_main_head" 2>/dev/null || echo "0")
  _pb_fb=$(head -c 1 "$_main_head" 2>/dev/null || echo "0")
  _pb_sig="${_pb_mtime}:${_pb_fb}"
  _pb_cached=""
  if [[ -f "$_pb_cache" ]]; then
    { IFS= read -r _pb_cached_sig && IFS= read -r _pb_cached; } < "$_pb_cache" 2>/dev/null
    [[ "$_pb_cached_sig" == "$_pb_sig" ]] || _pb_cached=""
  fi
  if [[ -z "$_pb_cached" ]]; then
    _pb_cached=$(git -C "$_main_repo_dir" branch --show-current 2>/dev/null)
    if [[ -z "$_pb_cached" ]]; then
      _pb_sha=$(git -C "$_main_repo_dir" rev-parse --short HEAD 2>/dev/null)
      _pb_cached="${_pb_sha:+@${_pb_sha}}"
    fi
    _pb_cached="${_pb_cached:-?}"
    printf '%s\n%s' "$_pb_sig" "$_pb_cached" > "$_pb_cache" 2>/dev/null
  fi
  if [ "${#_pb_cached}" -gt 14 ]; then primary_branch_display="${_pb_cached:0:13}…"
  else                                  primary_branch_display="$_pb_cached"; fi
fi

# --- Tmux session (single call for both name and session_id) ---
tmux_part=""
_telem_session="unknown"
if [ -n "$TMUX" ]; then
  _tmux_info=$(tmux display-message -p '#{session_name}|#{session_id}' 2>/dev/null)
  tmux_session="${_tmux_info%|*}"
  _telem_session="${_tmux_info#*|}"
  [ -n "$tmux_session" ] && tmux_part="\033[35m${tmux_session}\033[0m"
fi

# --- Rotating tips + OS-aware prompt-box shortcuts (every 45 seconds) ---
tips=("/compact to free context" "Shift+Tab accepts edits" "#file adds files to context"
  "/cost for detailed costs" "/memory to view memories" "Esc interrupts Claude"
  "/code-review for uncommitted changes" "Tab Tab: accept + run" "/model to switch models"
  "@ to reference commits or PRs" "Ctrl+R: search history" "/clear resets context"
  "Paste or drag images to context" "/vim for vim-mode" "--- adds a thinking pause"
  "\\ for multi-line input" "claude -p for non-interactive" "/hooks views automation"
  "? for all shortcuts")
# Prompt-box shortcuts — word-nav modifier differs by OS
_ai_host="${AI_HOST:-}"
if [[ "$_ai_host" == "mac" ]] || [[ "$(uname -s 2>/dev/null)" == "Darwin" ]]; then
  tips+=("Ctrl+A/E: line start/end" "Opt+←/→: word back/fwd"
    "Ctrl+W: del prev word" "Ctrl+U/K: del to start/end"
    "Shift+Enter: new line" "/shortcuts for full list")
elif [[ "$_ai_host" == *"windows"* ]]; then
  tips+=("Ctrl+A/E: line start/end" "Alt+←/→: word back/fwd (Git Bash)"
    "Ctrl+W: del prev word" "Ctrl+U/K: del to start/end"
    "Shift+Enter: new line" "/shortcuts for full list")
else
  tips+=("Ctrl+A/E: line start/end" "Alt+←/→: word back/fwd"
    "Ctrl+W: del prev word" "Ctrl+U/K: del to start/end"
    "Shift+Enter: new line" "/shortcuts for full list")
fi
tip_index=$(( $(date +%s) / 45 % ${#tips[@]} ))
tip="\033[2;3m${tips[$tip_index]}\033[0m"

# --- Clock ---
clock=$(date +%H:%M)

# --- Billing mode ---
_billing_mode="${CC_BILLING_MODE:-subscription}"

quota_part=""

sess_24h_part=""
agg_24h_part=""
agg_7d_part=""
cache_hit_part=""

if [[ "$_billing_mode" == "api" ]]; then
  # API billing cost items (session $, 24h:, Σ24h:, Σ7d:) REMOVED 2026-06-08 pending
  # an accuracy fix — the dollar figures were wildly inflated (see roadmap AIH-23).
  # The per-session cost-write infrastructure (~/.claude/.sl-sessions) is gone with
  # them; only the cache-hit indicator remains on API-billing machines.
  _dc_now=$(date +%s)

  # --- Cache hit rate (JSONL scanner, stale-while-revalidate, 10-min TTL) ---
  # ~/.claude/cache-stats-worker.py scans ~/.claude/projects/**/*.jsonl for last 24h
  # and outputs: line 1 = epoch, line 2 = cache_hit_pct integer (0-100).
  # Run at most once per 10 min; background refresh when 5-10 min stale.
  _chcache="$HOME/.claude/.sl-cache-hit"
  _chlock="$HOME/.claude/.sl-cache-hit-lock"
  _ch_valid=0; _ch_pct=0; _ch_ts=0
  if [[ -f "$_chcache" ]]; then
    { IFS= read -r _ch_ts && IFS= read -r _ch_pct; } < "$_chcache" 2>/dev/null
    if [[ "$_ch_ts" =~ ^[0-9]+$ ]]; then
      _ch_age=$(( _dc_now - _ch_ts ))
      if (( _ch_age < 600 )); then
        _ch_valid=1
        if (( _ch_age >= 300 )); then   # stale — trigger background refresh
          _ch_need=1
          if [[ -f "$_chlock" ]]; then
            IFS= read -r _chtl < "$_chlock" 2>/dev/null
            [[ "$_chtl" =~ ^[0-9]+$ ]] && (( _dc_now - _chtl <= 180 )) && _ch_need=0
          fi
          (( _ch_need )) && {
            printf '%d' "$_dc_now" > "$_chlock" 2>/dev/null
            ( python3 "$HOME/.claude/cache-stats-worker.py" > "$_chcache" 2>/dev/null
              rm -f "$_chlock" ) >/dev/null 2>&1 &
          }
        fi
      fi
    fi
  fi
  if (( ! _ch_valid )); then
    timeout 8 python3 "$HOME/.claude/cache-stats-worker.py" > "$_chcache" 2>/dev/null
    { IFS= read -r _ch_ts && IFS= read -r _ch_pct; } < "$_chcache" 2>/dev/null
  fi
  if [[ "$_ch_pct" =~ ^[0-9]+$ ]] && (( _ch_pct > 0 )); then
    if   (( _ch_pct >= 80 )); then cache_hit_part="\033[32m↑${_ch_pct}%\033[0m"
    elif (( _ch_pct >= 60 )); then cache_hit_part="\033[33m↑${_ch_pct}%\033[0m"
    else                           cache_hit_part="\033[31m↑${_ch_pct}%\033[0m"; fi
  fi

else
  # Subscription billing: show OAuth quota via ai CLI.

  # --- AIH-164 T-01: capture CC rate_limits (stdin) → account-global quota.json + env ---
  # CC v2.1.80+ hands the statusLine command a rate_limits object on stdin:
  #   .rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}
  # This is the official, $0/zero-token source for the weekly all-models % + 5h window +
  # resets (replaces the /usage scrape for those). Extract it, write an account-global cache
  # (~/.claude/state/quota.json) for the ad-hoc quota-pct tool, and EXPORT the values here —
  # before the quota block below — so BOTH `ai quota statusline-part` call sites (the cold
  # synchronous path AND the backgrounded stale-while-revalidate subshell) inherit them and
  # can use rate_limits as the authoritative source (F-03). Absent (enterprise/non-Pro-Max,
  # or pre-first-response) → leave any existing cache untouched, export nothing, still render.
  _rl=$(echo "$input" | jq -r '
    if (.rate_limits.seven_day.used_percentage != null) then
      "\(.rate_limits.seven_day.used_percentage)\t\(.rate_limits.seven_day.resets_at // "")\t\(.rate_limits.five_hour.used_percentage // "")\t\(.rate_limits.five_hour.resets_at // "")"
    else empty end' 2>/dev/null)
  if [ -n "$_rl" ]; then
    IFS=$'\t' read -r _sd_pct _sd_reset _fh_pct _fh_reset <<< "$_rl"
    export AI_CLI_QUOTA_SEVEN_DAY_PCT="$_sd_pct"
    export AI_CLI_QUOTA_SEVEN_DAY_RESET="$_sd_reset"
    export AI_CLI_QUOTA_FIVE_HOUR_PCT="$_fh_pct"
    export AI_CLI_QUOTA_FIVE_HOUR_RESET="$_fh_reset"
    _qstate_dir="$HOME/.claude/state"
    _qstate="$_qstate_dir/quota.json"
    # `// null` coalesce is REQUIRED (audit N-01): CC may omit any rate_limits sub-field
    # ("each window may be independently absent"); a bare `("" | tonumber?)` yields `empty`,
    # which collapses the ENTIRE jq object → no output → quota.json silently never written.
    # Coalescing each optional field to null keeps the object (with a valid seven_day_pct).
    _qnew=$(jq -cn --argjson sd "$_sd_pct" --arg sdr "$_sd_reset" \
                   --arg fh "$_fh_pct" --arg fhr "$_fh_reset" \
                   --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
      {seven_day_pct:$sd,
       seven_day_reset:(($sdr|tonumber?) // null),
       five_hour_pct:(($fh|tonumber?) // null),
       five_hour_reset:(($fhr|tonumber?) // null),
       updated_at:$ts, source:"statusline_rate_limits"}' 2>/dev/null)
    if [ -n "$_qnew" ]; then
      # De-dup guard (F-09; audit N-02): skip the atomic write when the account-global numbers
      # are unchanged — avoids churn. No monotonic/anti-regression guard: CC hands each session
      # the CURRENT account-global rate_limits, so there is no stale-payload path, and pct is not
      # monotonic across the weekly reset. Compare pct+reset only (updated_at always differs).
      _qwrite=1
      if [ -f "$_qstate" ]; then
        _cmp_old=$(jq -S '{seven_day_pct,seven_day_reset,five_hour_pct,five_hour_reset}' "$_qstate" 2>/dev/null)
        _cmp_new=$(echo "$_qnew" | jq -S '{seven_day_pct,seven_day_reset,five_hour_pct,five_hour_reset}' 2>/dev/null)
        [ -n "$_cmp_old" ] && [ "$_cmp_old" = "$_cmp_new" ] && _qwrite=0
      fi
      if (( _qwrite )); then
        mkdir -p "$_qstate_dir" 2>/dev/null
        printf '%s\n' "$_qnew" > "$_qstate.tmp.$$" 2>/dev/null && mv -f "$_qstate.tmp.$$" "$_qstate" 2>/dev/null
      fi
    fi
  fi

  # Telemetry (rate-limited fire-and-forget)
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
  if (( _do_record )) && [[ "${AI_HOST:-}" != "acn-windows" ]]; then
    printf '%d' "$(date +%s)" > "$_tcache" 2>/dev/null
    ai quota record "${_telem_session}" "$(hostname)" "${model_id}" "${_telem_tokens}" >/dev/null 2>&1 &
  fi

  # Quota indicator (cached, stale-while-revalidate)
  # ai quota statusline-part takes ~700ms (Python startup + SQLite + optional NATS check).
  # CC calls statusLine on every render cycle — during streaming that's many times/sec.
  # Stale-while-revalidate pattern: serve the cached value (even if stale) and kick off
  # a background refresh. This prevents concurrent invocations from all blocking on the
  # slow call at the same time, which caused duplicate prompt boxes in the scrollback buffer
  # (all concurrent calls completed near-simultaneously and each wrote a full status line).
  #
  # Cache format: line 1 = Unix timestamp written, line 2 = quota output (may be empty).
  # Zones: < 30s = fresh (serve directly); 30s–300s = stale (serve + background refresh);
  # > 300s = very old (synchronous fetch, only on first-ever call or after long gaps).
  # Lock file prevents concurrent background refreshes from stampeding.
  _qcache="${TMPDIR:-/tmp}/.ai-sl-quota-${UID:-0}"
  _qlock="${TMPDIR:-/tmp}/.ai-sl-quota-lock-${UID:-0}"
  _quota_cache_valid=0
  _qnow=$(date +%s)
  if [[ -f "$_qcache" ]]; then
    { IFS= read -r _qts && IFS= read -r quota_part; } < "$_qcache" 2>/dev/null
    if [[ "$_qts" =~ ^[0-9]+$ ]]; then
      _qage=$(( _qnow - _qts ))
      if (( _qage < 30 )); then
        _quota_cache_valid=1  # fresh — serve directly
      elif (( _qage < 300 )); then
        _quota_cache_valid=1  # stale — serve this cycle, refresh in background
        # Only launch one background refresh at a time (lock file dedup)
        _need_refresh=1
        if [[ -f "$_qlock" ]]; then
          IFS= read -r _qlts < "$_qlock" 2>/dev/null
          [[ "$_qlts" =~ ^[0-9]+$ ]] && (( _qnow - _qlts <= 120 )) && _need_refresh=0
          (( _need_refresh )) && rm -f "$_qlock" 2>/dev/null
        fi
        if (( _need_refresh )); then
          printf '%d' "$_qnow" > "$_qlock" 2>/dev/null
          ( _qfresh=$(AI_CLI_STATUSLINE_COLS="${COLUMNS:-0}" ai quota statusline-part 2>/dev/null)
            _qfresh="${_qfresh//$'\n'/ }"
            printf '%d\n%s\n' "$(date +%s)" "$_qfresh" > "$_qcache" 2>/dev/null
            rm -f "$_qlock" 2>/dev/null
          ) >/dev/null 2>&1 &
        fi
      fi
    fi
  fi
  if (( ! _quota_cache_valid )); then
    # No cache or very old (>300s): synchronous fetch — only on first call or after a long gap.
    quota_part=$(AI_CLI_STATUSLINE_COLS="${COLUMNS:-0}" ai quota statusline-part 2>/dev/null)
    quota_part="${quota_part//$'\n'/ }"
    printf '%d\n%s' "$(date +%s)" "$quota_part" > "$_qcache" 2>/dev/null
  fi

fi

# --- Weekly quota reset datetime (AIH-239) ---
# The weekly all-models ("all sessions") reset and the weekly Fable cap reset are the
# SAME cycle, so one datetime covers both. Rendered as its own segment after the quota
# part (weekly + Fable) and before the tip. Prefer the value exported from this render's
# rate_limits; fall back to the persisted quota.json cache. Epoch seconds → human date,
# portable across macOS (date -r) and Linux/Hetzner (date -d @).
reset_part=""
_wk_reset="${AI_CLI_QUOTA_SEVEN_DAY_RESET:-}"
[ -z "$_wk_reset" ] && _wk_reset=$(jq -r '.seven_day_reset // empty' "$HOME/.claude/state/quota.json" 2>/dev/null)
if [[ "$_wk_reset" =~ ^[0-9]+$ ]]; then
  if _wk_str=$(date -r "$_wk_reset" '+%a %-m/%-d %H:%M' 2>/dev/null); then :
  elif _wk_str=$(date -d "@$_wk_reset" '+%a %-m/%-d %H:%M' 2>/dev/null); then :
  else _wk_str=""; fi
  [ -n "$_wk_str" ] && reset_part="\033[2mwk↻ ${_wk_str}\033[0m"
fi

# --- Assemble ---
sep="\033[2m│\033[0m"
if [[ -n "$worktree_name" ]]; then
  wt_display="${worktree_name:0:18}"
  [[ "${#worktree_name}" -gt 18 ]] && wt_display="${worktree_name:0:17}…"
  line="\033[2m${clock}\033[0m ${sep} ${model_short} ${sep} \033[1m${project}\033[0m:\033[36m${primary_branch_display}\033[0m \033[35m⎇\033[0m \033[2m${wt_display}\033[0m"
else
  line="\033[2m${clock}\033[0m ${sep} ${model_short} ${sep} \033[1m${project}\033[0m:\033[36m${branch_display}\033[0m"
fi
# Show the tmux session name ONLY when there's no worktree name. For ai-cli worktree
# sessions the worktree segment (⎇ <name>) already identifies the session, so the tmux
# session name (e.g. "c-aih-2" vs worktree "aih-2") is redundant — drop it to free space.
[ -n "$tmux_part" ] && [ -z "$worktree_name" ] && line="${line} ${sep} ${tmux_part}"
line="${line} ${sep} ${ctx_part}"
[ -n "$quota_part" ] && line="${line} ${sep} ${quota_part}"
[ -n "$reset_part" ] && line="${line} ${sep} ${reset_part}"
[ -n "$sess_24h_part" ] && line="${line} ${sep} ${sess_24h_part}"
[ -n "$agg_24h_part" ] && line="${line} ${sep} ${agg_24h_part}"
[ -n "$agg_7d_part" ] && line="${line} ${sep} ${agg_7d_part}"
[ -n "$cache_hit_part" ] && line="${line} ${sep} ${cache_hit_part}"
line="${line} ${sep} ${tip}"
# \033[0m resets colors; \033[K erases from cursor to end of line.
# When CC positions the cursor at the start of the status line and writes our output,
# \033[K clears any leftover characters from a previously longer render — preventing
# stale character artifacts from accumulating in the scrollback buffer.
_sl_out=$(printf '%b\033[0m\033[K' "$line")
printf '%s\n' "$_sl_out"
printf '%s\n' "$_sl_out" > "$_sl_last" 2>/dev/null   # cache for the single-flight skip path
