#!/bin/bash
# scripts/airflow-watch.sh — background Airflow pipeline/task monitor for AI sessions
#
# Runs as a CC/Gemini background process (Bash tool with run_in_background=true).
# The AI session is notified on exit and reacts without polling in-conversation.
# Zero tokens consumed while this script sleeps.
#
# The watcher is a pure notification layer — killing it does NOT affect the
# Airflow pipeline itself. Airflow runs independently via its scheduler.
#
# Usage:
#   bash scripts/airflow-watch.sh <dag_id> <run_id> [task_id]
#
#   dag_id   — Airflow DAG ID
#   run_id   — Airflow DAG run ID (e.g. manual__2026-04-23T05:37:30+00:00)
#   task_id  — (optional) specific task to watch; if omitted, watches whole DAG run
#
# Exit codes:
#   0 = success       — AI session should proceed with next steps
#   1 = failed        — AI session should diagnose, fix, re-trigger, re-launch watcher
#   2 = timeout (12h) — AI session should check Airflow UI for current state
#
# Sentinel/PID files (scoped to the triggering CC session's project root so
# other CC sessions' hooks don't fire false positives):
#   ${project_root}/.claude/state/airflow-watch.pid        — written at startup, removed on clean exit
#   ${project_root}/.claude/state/airflow-watcher-needed   — written by airflow-trigger.sh, cleared here
#
# project_root = $CLAUDE_PROJECT_DIR (set by CC) → git toplevel → $PWD.
# .claude/state/ is gitignored.
#
# The PostToolUse hook (airflow-watcher-required.sh) blocks CC/Gemini until this
# script is launched. The UserPromptSubmit hook (airflow-watcher-resume.sh)
# recreates the sentinel if the AI session restarted while the watcher was
# running — ensuring the watcher can never be silently forgotten.

set -u

DAG_ID="${1:?Usage: airflow-watch.sh <dag_id> <run_id> [task_id]}"
RUN_ID="${2:?Usage: airflow-watch.sh <dag_id> <run_id> [task_id]}"
TASK_ID="${3:-}"

POLL_INTERVAL="${AIRFLOW_WATCH_INTERVAL:-60}"
MAX_RUNTIME=$(( 12 * 3600 ))
START_TIME=$(date +%s)

HETZNER_HOST="${HETZNER_HOST:-sergei@178.104.70.139}"
AIRFLOW_BIN="${AIRFLOW_BIN:-/home/sergei/airflow-venv/bin/airflow}"

# Run airflow locally on Hetzner, otherwise SSH for each call.
if [[ "${AI_CLI_HOST:-mac}" == "hetzner" ]]; then
    af() { "$AIRFLOW_BIN" "$@" 2>/dev/null; }
else
    af() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HETZNER_HOST" \
               "$AIRFLOW_BIN $(printf '%q ' "$@")" 2>/dev/null; }
fi

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE_DIR="${PROJECT_ROOT}/.claude/state"
mkdir -p "$STATE_DIR"
PID_FILE="${STATE_DIR}/airflow-watch.pid"
SENTINEL="${STATE_DIR}/airflow-watcher-needed"

cat > "$PID_FILE" <<EOF
PID=$$
DAG_ID=${DAG_ID}
RUN_ID=${RUN_ID}
TASK_ID=${TASK_ID}
OWNER_DIR=${PROJECT_ROOT}
STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
WATCH_CMD=bash scripts/airflow-watch.sh ${DAG_ID} ${RUN_ID} ${TASK_ID}
EOF

rm -f "$SENTINEL"
trap 'rm -f "$PID_FILE"' EXIT

log() { echo "[airflow-watch $(date '+%H:%M:%S')] $*"; }

get_dag_state() {
    af dags list-runs --dag-id "$DAG_ID" \
      | awk -F'|' -v run="$RUN_ID" \
          'NR>2 && NF>1 { gsub(/ /,"",$2); gsub(/ /,"",$3); if ($2==run) { print $3; exit } }'
}

get_task_state() {
    af tasks states-for-dag-run "$DAG_ID" "$RUN_ID" \
      | awk -F'|' -v task="$TASK_ID" \
          'NF>1 { t=$3; gsub(/ /,"",t); s=$4; gsub(/ /,"",s); if (t==task) { print s; exit } }'
}

dump_task_states() {
    echo "--- Task states ---"
    af tasks states-for-dag-run "$DAG_ID" "$RUN_ID" | tail -30 || true
    echo "-------------------"
}

if [[ -n "$TASK_ID" ]]; then
    log "=== START watching task=${TASK_ID} dag=${DAG_ID} run=${RUN_ID} ==="
else
    log "=== START watching dag=${DAG_ID} run=${RUN_ID} ==="
fi

# Initial probe so startup failures surface immediately rather than after 60s.
if ! af version >/dev/null; then
    log "ERROR: cannot reach Airflow on ${HETZNER_HOST} — is the scheduler up?"
    exit 1
fi

while true; do
    ELAPSED=$(( $(date +%s) - START_TIME ))
    ELAPSED_H=$(( ELAPSED / 3600 ))
    ELAPSED_M=$(( (ELAPSED % 3600) / 60 ))

    if [[ $ELAPSED -gt $MAX_RUNTIME ]]; then
        log "TIMEOUT after ${ELAPSED_H}h${ELAPSED_M}m — check Airflow UI manually"
        dump_task_states
        exit 2
    fi

    if [[ -n "$TASK_ID" ]]; then
        STATE=$(get_task_state)
    else
        STATE=$(get_dag_state)
    fi

    log "state=${STATE:-unknown} (${ELAPSED_H}h${ELAPSED_M}m elapsed)"

    case "$STATE" in
        success)
            log "=== SUCCESS (${ELAPSED_H}h${ELAPSED_M}m) ==="
            dump_task_states
            exit 0
            ;;
        failed)
            log "=== FAILED (${ELAPSED_H}h${ELAPSED_M}m) ==="
            dump_task_states
            exit 1
            ;;
    esac

    sleep "$POLL_INTERVAL"
done
