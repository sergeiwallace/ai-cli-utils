#!/usr/bin/env bash
# wt-branch-push-guard.sh — pre-push guard against publishing a canonical
# worktree branch (wt-*) to a same-named ref on the remote (AI-CLI-128).
#
# Canonical `ai c N` worktree branches (wt-*) are working-directory checkouts,
# never development branches — they ship via `git push origin HEAD:main`, and
# their upstream must always be origin/main. This hook is the survivor layer:
# it catches the case where a worktree branch reaches a push with no upstream
# (or an already-wrong upstream) and someone follows git's own advice —
#   git push --set-upstream origin wt-X
# — which publishes an `origin/wt-X` branch instead of shipping to main. This
# happened for real: ai-ide-mobile/mobile-1 sat 46 commits behind main while
# reporting "0 ahead, 0 behind" against its own dead upstream (AI-CLI-128).
#
# pre-commit does not forward the raw git pre-push stdin protocol to local
# hooks (verified empirically, AI-CLI-128) — it exposes the ref pair via
# PRE_COMMIT_LOCAL_BRANCH / PRE_COMMIT_REMOTE_BRANCH env vars instead.
#
# Bypass (should not be needed in normal use): SKIP=wt-branch-push-guard git push
set -uo pipefail

local_branch="${PRE_COMMIT_LOCAL_BRANCH:-}"
remote_branch="${PRE_COMMIT_REMOTE_BRANCH:-}"

local_name="${local_branch#refs/heads/}"
remote_name="${remote_branch#refs/heads/}"

case "$local_name" in
    wt-*)
        if [ "$local_name" = "$remote_name" ]; then
            echo "" >&2
            echo "[wt-branch-push-guard] REJECTED: pushing canonical worktree branch '$local_name' to a same-named remote ref." >&2
            echo "  Worktree branches are working-directory checkouts, never development branches." >&2
            echo "  Ship with:   git push origin HEAD:main" >&2
            echo "  Never:       git push --set-upstream origin $local_name" >&2
            echo "" >&2
            exit 1
        fi
        ;;
esac

exit 0
