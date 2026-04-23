#!/bin/bash
# PostToolUse hook: enforce watcher launch after every Airflow pipeline trigger.
#
# Checks /tmp/airflow_watcher_needed (written by scripts/airflow-trigger.sh).
# If the sentinel exists, blocks the session with exit 2 and prints the exact
# command to launch. scripts/airflow-watch.sh deletes the sentinel at startup,
# lifting the block. Fast no-op when no sentinel is present.

SENTINEL="/tmp/airflow_watcher_needed"
[ -f "$SENTINEL" ] || exit 0

DAG_ID=$(grep       '^DAG_ID='      "$SENTINEL" 2>/dev/null | cut -d= -f2-)
RUN_ID=$(grep       '^RUN_ID='      "$SENTINEL" 2>/dev/null | cut -d= -f2-)
TASK_ID=$(grep      '^TASK_ID='     "$SENTINEL" 2>/dev/null | cut -d= -f2-)
WATCHER_CMD=$(grep  '^WATCHER_CMD=' "$SENTINEL" 2>/dev/null | cut -d= -f2-)

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  AIRFLOW WATCHER NOT LAUNCHED — action required before proceeding   ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  DAG:  ${DAG_ID}"
echo "║  Run:  ${RUN_ID}"
echo "║  Task: ${TASK_ID}"
echo "║                                                                      ║"
echo "║  A DAG was triggered but no watcher is running. Launch this as a   ║"
echo "║  Bash tool call with run_in_background=true:                        ║"
echo "║                                                                      ║"
echo "║    ${WATCHER_CMD}"
echo "║                                                                      ║"
echo "║  Exit codes: 0=success 1=failed 2=timeout(12h)                      ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

exit 2
