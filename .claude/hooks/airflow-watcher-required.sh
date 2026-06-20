#!/bin/bash
# PostToolUse hook: enforce watcher launch after an Airflow pipeline trigger
# from THIS CC session only.
#
# The sentinel is project-root-scoped (via $CLAUDE_PROJECT_DIR or git worktree
# root), so only the CC session that triggered the pipeline blocks — other
# sessions (different worktrees / projects) see no sentinel and pass through.
# scripts/airflow-watch.sh deletes the sentinel at startup, lifting the block.
# Fast no-op when no sentinel is present.
#
# Pass-through policy:
#   Poll up to 5s for the watcher PID file to appear. The watcher writes its
#   PID file as its first action — this handles the run_in_background race
#   condition where PostToolUse fires before the background process initializes.
#   If a live watcher PID is found, clear the sentinel and exit 0.
#   Block only when no live watcher is found after the poll window.

# AIH-55: Airflow is personal-machines-only (Hetzner/Mac). Hard no-op on BMS
# machines — they never run Airflow. Keeps this hook fully inert (no state dir,
# no debug log, no blocking) when a session runs on a BMS host.
case "${AI_HOST:-}" in
  cld-platform | bms-windows) exit 0 ;;
esac

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
SENTINEL="${PROJECT_ROOT}/.claude/state/airflow-watcher-needed"

# Debug log — always write so we can trace hook invocations even when no sentinel.
LOG="${PROJECT_ROOT}/.claude/state/hook-debug.log"
mkdir -p "${PROJECT_ROOT}/.claude/state"
echo "[$(date '+%H:%M:%S')] hook fired | CLAUDE_PROJECT_DIR=${CLAUDE_PROJECT_DIR:-UNSET} | PROJECT_ROOT=${PROJECT_ROOT} | sentinel=$([ -f "$SENTINEL" ] && echo EXISTS || echo absent)" >> "$LOG"

[ -f "$SENTINEL" ] || exit 0

echo "[$(date '+%H:%M:%S')] sentinel EXISTS — polling for watcher PID (up to 5s)" >> "$LOG"

# Poll for watcher PID — handles the run_in_background race condition where
# PostToolUse fires before the background process writes its PID file.
PID_FILE="${PROJECT_ROOT}/.claude/state/airflow-watch.pid"
for _i in 1 2 3 4 5; do
    if [ -f "$PID_FILE" ]; then
        WATCHER_PID=$(grep '^PID=' "$PID_FILE" 2>/dev/null | cut -d= -f2-)
        if [ -n "$WATCHER_PID" ] && kill -0 "$WATCHER_PID" 2>/dev/null; then
            echo "[$(date '+%H:%M:%S')] PASS-THROUGH (PID ${WATCHER_PID} alive at poll ${_i}, sentinel cleared)" >> "$LOG"
            rm -f "$SENTINEL"
            exit 0
        fi
        echo "[$(date '+%H:%M:%S')] poll ${_i}: PID file exists but PID=${WATCHER_PID} is dead" >> "$LOG"
    else
        echo "[$(date '+%H:%M:%S')] poll ${_i}: no PID file yet" >> "$LOG"
    fi
    sleep 1
done

echo "[$(date '+%H:%M:%S')] BLOCKING — no live watcher after 5s poll" >> "$LOG"

# Sentinel exists but watcher is not running — block and prompt relaunch.
DAG_ID=$(grep       '^DAG_ID='      "$SENTINEL" 2>/dev/null | cut -d= -f2-)
RUN_ID=$(grep       '^RUN_ID='      "$SENTINEL" 2>/dev/null | cut -d= -f2-)
TASK_ID=$(grep      '^TASK_ID='     "$SENTINEL" 2>/dev/null | cut -d= -f2-)
WATCHER_CMD=$(grep  '^WATCHER_CMD=' "$SENTINEL" 2>/dev/null | cut -d= -f2-)

echo "[$(date '+%H:%M:%S')] BLOCKING — sentinel exists, watcher dead/missing" >> "$LOG"

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  AIRFLOW WATCHER DIED — relaunch required before proceeding         ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  DAG:  ${DAG_ID}"
echo "║  Run:  ${RUN_ID}"
echo "║  Task: ${TASK_ID}"
echo "║                                                                      ║"
echo "║  The watcher process is no longer running. Relaunch it as a Bash   ║"
echo "║  tool call with run_in_background=true:                             ║"
echo "║                                                                      ║"
echo "║    ${WATCHER_CMD}"
echo "║                                                                      ║"
echo "║  Exit codes: 0=success 1=failed 2=timeout(12h)                      ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

exit 2
