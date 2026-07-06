#!/usr/bin/env bash
# roadmap-guard — pre-push stage via the pre-commit framework (AIH-89 T-02).
#
# Rejects a push that INTRODUCES a duplicate `PREFIX-N` roadmap entry id. IDs already
# duplicated on origin/main are grandfathered, so enabling the hook never blocks an
# unrelated push. Only entry-defining lines count (`- [ ] `[Pn]` `[PREFIX-N]` …`), so
# prose mentions like `Related: AIH-58` are ignored.
#
# Conflict markers are handled separately by the standard `check-merge-conflict` hook
# (commit stage), so this hook is dup-id only.
#
# Stopgap: retired at the SW-841 (Beads) cutover, where hash ids make PREFIX-N moot.
#
# Bypass:  SKIP=roadmap-guard git push
set -uo pipefail

ROADMAP="docs/roadmap/master-roadmap.md"

# No roadmap committed at HEAD -> nothing to guard (most repos).
git cat-file -e "HEAD:$ROADMAP" 2>/dev/null || exit 0

# Duplicated entry ids in a roadmap fed on stdin. An entry line is
# `- [ ]`/`- [x]` + a `[Pn]` priority tag + the entry's own `[PREFIX-N]` id.
_dups() {
    grep -oE '^- \[[ xX]\] `\[P[0-9]\]` `\[[A-Z][A-Z0-9-]*-[0-9]+\]`' \
        | sed -E 's/.*`\[([A-Z][A-Z0-9-]*-[0-9]+)\]`$/\1/' \
        | sort | uniq -d
}

head_dups="$(git show "HEAD:$ROADMAP" 2>/dev/null | _dups | sort -u)"
base_dups="$(git show "origin/main:$ROADMAP" 2>/dev/null | _dups | sort -u)"

# ids duplicated at HEAD but not already duplicated on origin/main
new_dups="$(comm -23 <(printf '%s\n' "$head_dups") <(printf '%s\n' "$base_dups") | grep -v '^$' || true)"

if [ -n "$new_dups" ]; then
    echo "roadmap-guard: this push INTRODUCES duplicate roadmap ID(s):" >&2
    printf '%s\n' "$new_dups" | sed 's/^/  - /' >&2
    echo "Renumber the duplicate entry (get a fresh id with: ai next-id <PREFIX>)," >&2
    echo "or bypass with: SKIP=roadmap-guard git push" >&2
    exit 1
fi
exit 0
