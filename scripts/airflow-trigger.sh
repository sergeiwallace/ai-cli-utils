#!/bin/bash
# scripts/airflow-trigger.sh — trigger an Airflow DAG run AND arm the watcher sentinel.
#
# Wraps `airflow dags trigger` + writes /tmp/airflow_watcher_needed so the
# PostToolUse hook forces the AI session to launch airflow-watch.sh as a
# background process before any further tool calls succeed.
#
# Usage:
#   bash scripts/airflow-trigger.sh <dag_id> [--conf <json>] [--task-id <task>]
#
#   dag_id     — Airflow DAG to trigger
#   --conf     — optional JSON conf for the DAG run (e.g. '{"artist":"schiele"}')
#   --task-id  — optional: after triggering, tell the watcher to watch just this
#                task; if omitted, the watcher monitors the whole DAG run
#
# After this script prints the watcher command, the AI session MUST launch it
# via the Bash tool with run_in_background=true. The PostToolUse hook blocks
# further tool calls until the watcher is running.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: bash scripts/airflow-trigger.sh <dag_id> [--conf <json>] [--task-id <task>]" >&2
    exit 2
fi

DAG_ID="$1"; shift
CONF=""
TASK_ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --conf)     CONF="$2"; shift 2 ;;
        --task-id)  TASK_ID="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

HETZNER_HOST="${HETZNER_HOST:-sergei@178.104.70.139}"
AIRFLOW_BIN="${AIRFLOW_BIN:-/home/sergei/airflow-venv/bin/airflow}"

if [[ "${AI_CLI_HOST:-mac}" == "hetzner" ]]; then
    af() { "$AIRFLOW_BIN" "$@"; }
else
    af() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HETZNER_HOST" \
               "$AIRFLOW_BIN $(printf '%q ' "$@")"; }
fi

echo "[airflow-trigger] Triggering DAG=$DAG_ID ${CONF:+conf=$CONF}"
if [[ -n "$CONF" ]]; then
    af dags trigger "$DAG_ID" --conf "$CONF"
else
    af dags trigger "$DAG_ID"
fi

# Give the scheduler a moment to register the run
sleep 3

RUN_ID=$(af dags list-runs --dag-id "$DAG_ID" 2>/dev/null \
    | awk -F'|' 'NR>2 && NF>1 { gsub(/ /,"",$2); print $2; exit }')

if [[ -z "$RUN_ID" ]]; then
    echo "[airflow-trigger] ERROR: could not resolve run_id after trigger" >&2
    exit 1
fi

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCH_CMD="bash ${SCRIPTS_DIR}/airflow-watch.sh ${DAG_ID} ${RUN_ID}"
[[ -n "$TASK_ID" ]] && WATCH_CMD="${WATCH_CMD} ${TASK_ID}"

# Sentinel lives in the triggering CC session's project root so other CC
# sessions' PostToolUse hooks don't see it. The hook resolves the same
# project root via $CLAUDE_PROJECT_DIR (set by CC) → git toplevel → $PWD.
PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE_DIR="${PROJECT_ROOT}/.claude/state"
mkdir -p "$STATE_DIR"
SENTINEL="${STATE_DIR}/airflow-watcher-needed"
cat > "$SENTINEL" <<EOF
DAG_ID=${DAG_ID}
RUN_ID=${RUN_ID}
TASK_ID=${TASK_ID}
TRIGGERED_AT=$(date '+%Y-%m-%d %H:%M:%S')
OWNER_DIR=${PROJECT_ROOT}
WATCHER_CMD=${WATCH_CMD}
EOF

echo ""
echo "[airflow-trigger] Run: ${RUN_ID}"
echo "[airflow-trigger] Sentinel: ${SENTINEL}"
echo "[airflow-trigger] PostToolUse hook in this CC session will block until watcher launches"
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  MANDATORY NEXT STEP: launch this as a background Bash tool call   ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  ${WATCH_CMD}"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  run_in_background=true  |  exit: 0=ok 1=fail 2=timeout(12h)       ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
