#!/usr/bin/env bash
# corrupt-index-guard.sh — pre-push guard against worktree index corruption.
#
# Detects the signature of a corrupted git index: a large number of staged
# deletions (D) that do not correspond to actual file removals.  This pattern
# arises when a rebase conflict leaves the index in an intermediate state.
# Pushing with a corrupt index causes spurious bulk-delete commits.
#
# If corruption is detected the push is aborted with recovery instructions.
#
# Environment overrides:
#   SKIP_INDEX_GUARD=1  — bypass this check (emergency escape hatch only)

set -uo pipefail

if [ "${SKIP_INDEX_GUARD:-0}" = "1" ]; then
    echo "[corrupt-index-guard] SKIP_INDEX_GUARD=1 — bypassing check"
    exit 0
fi

THRESHOLD=${CORRUPT_INDEX_THRESHOLD:-50}

deleted_count=$(git diff --cached --name-only --diff-filter=D 2>/dev/null | wc -l | tr -d ' ')

if [ "$deleted_count" -gt "$THRESHOLD" ]; then
    echo ""
    echo "[corrupt-index-guard] ERROR: index corruption detected."
    echo "  $deleted_count staged deletions found (threshold: $THRESHOLD)."
    echo "  This is the signature of a corrupted worktree index after a"
    echo "  failed or partial rebase — these are not real file deletions."
    echo ""
    echo "  Recovery:"
    echo "    git read-tree HEAD"
    echo "    git update-index --refresh"
    echo "    git status   # should be clean or show only real changes"
    echo ""
    echo "  Then re-run your push.  To bypass (emergency only):"
    echo "    SKIP_INDEX_GUARD=1 git push ..."
    echo ""
    exit 1
fi

exit 0
