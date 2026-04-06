#!/bin/bash
# Fresh session orientation hook (SessionStart)
# Detects new conversations and injects a message to read docs/README.md.
#
# Uses the SessionStart event's "source" field:
#   "startup" = brand new conversation → inject orientation
#   "resume"  = resumed conversation → skip
#   "clear"   = cleared conversation → inject orientation
#   "compact" = compacted conversation → skip
#
# Oriented session IDs are tracked in ~/.claude/.sessions-oriented
# to prevent re-orientation if a session is restarted.

set -euo pipefail

# Read JSON from stdin
INPUT=$(cat)

# Parse source and session_id
SOURCE=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('source',''))" 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null || echo "")

# Only orient on new or cleared conversations
if [[ "$SOURCE" != "startup" && "$SOURCE" != "clear" ]]; then
  exit 0
fi

# Check if this session was already oriented
ORIENTED_FILE="$HOME/.claude/.sessions-oriented"
if [[ -f "$ORIENTED_FILE" ]] && grep -qF "$SESSION_ID" "$ORIENTED_FILE" 2>/dev/null; then
  exit 0
fi

# Check if docs/README.md exists in the project
PROJECT_DIR=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('cwd',''))" 2>/dev/null || echo "")
if [[ ! -f "$PROJECT_DIR/docs/README.md" ]]; then
  exit 0
fi

# Mark as oriented
echo "$SESSION_ID" >> "$ORIENTED_FILE"

# Inject orientation message
cat << 'EOF'
{"message": "⚠️ Fresh session detected. Before responding to the user's first message, read `docs/README.md` for repo structure orientation. This tells you what each docs/ subdirectory is for — check it before creating or moving docs."}
EOF
