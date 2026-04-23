#!/bin/bash
# PostToolUse hook: enforce watcher launch after an Airflow pipeline trigger
# from THIS CC session only.
#
# The sentinel is project-root-scoped (via $CLAUDE_PROJECT_DIR or git worktree
# root), so only the CC session that triggered the pipeline blocks — other
# sessions (different worktrees / projects) see no sentinel and pass through.
# scripts/airflow-watch.sh deletes the sentinel at startup, lifting the block.
# Fast no-op when no sentinel is present.

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
SENTINEL="${PROJECT_ROOT}/.claude/state/airflow-watcher-needed"
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
