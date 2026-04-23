#!/bin/bash
# UserPromptSubmit hook: detect stale watcher PID after AI session restart.
#
# The PID file is project-root-scoped so this only fires for the CC session
# that launched the watcher — other sessions see no file and pass through.
#
# scripts/airflow-watch.sh writes the PID file at startup and removes it on
# clean exit. If the session restarts while a watcher was running, the PID
# file remains but the process is dead. This hook detects that state and
# recreates the sentinel, which triggers the PostToolUse hook to block on the
# next tool call until the watcher is relaunched.

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE_DIR="${PROJECT_ROOT}/.claude/state"
PID_FILE="${STATE_DIR}/airflow-watch.pid"
[ -f "$PID_FILE" ] || exit 0

# shellcheck disable=SC1090
source "$PID_FILE"

if kill -0 "$PID" 2>/dev/null; then
    exit 0
fi

SENTINEL="${STATE_DIR}/airflow-watcher-needed"
cat > "$SENTINEL" <<EOF
DAG_ID=${DAG_ID}
RUN_ID=${RUN_ID}
TASK_ID=${TASK_ID}
TRIGGERED_AT=${STARTED_AT} (resumed after session restart)
OWNER_DIR=${PROJECT_ROOT}
WATCHER_CMD=${WATCH_CMD}
EOF

rm -f "$PID_FILE"

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  WATCHER RESUMED AFTER SESSION RESTART                              ║"
echo "║  An Airflow watcher was running before this session exited.        ║"
echo "║  Sentinel recreated — the PostToolUse hook will block on your next ║"
echo "║  Bash call until you relaunch:                                      ║"
echo "║    ${WATCH_CMD}"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
