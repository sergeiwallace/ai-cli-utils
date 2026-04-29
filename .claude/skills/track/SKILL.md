---
name: track
description: Scan recent conversation context and add untracked work items as CC tasks and roadmap entries.
---

# track

Context-aware task tracker. Scans the recent conversation for work items requested or
discussed that aren't currently tracked as CC tasks, then creates CC tasks (and roadmap
entries for significant items) to capture them.

**Usage:** `/track`

## Behavior

1. Call `TaskList` to see all existing CC tasks (pending + in-progress).
2. Review the recent conversation context for requested work items — features, fixes,
   research, docs, propagation, config changes.
3. Identify gaps: items that were requested or agreed upon but have no CC task.
4. For each untracked item, decide scope:
   - **Significant** (new feature, substantial refactor, design, research run, propagation
     affecting multiple repos): create a CC task **and** a roadmap entry in
     `docs/roadmap/master-roadmap.md`. Use the project's task prefix from `sergei.toml`
     and call `get_next_task_id(prefix)` for the display ID.
   - **Minor** (small fix, single-file doc tweak, formatting, one-liner config): create a
     CC task only — no roadmap entry needed.
   - **Marginal** (cosmetic, already implied by a larger task, clearly in-scope of an
     open task): skip entirely — it's already covered.
5. Report a summary: what was added (task ID, subject, roadmap entry Y/N), what was skipped and why.

## Rules

- Never duplicate an existing CC task or roadmap entry — fold related work into the
  closest existing item instead and note the update.
- Use the standard CC task subject format: `PREFIX-N #cc-id: description` when a roadmap
  entry exists, `#cc-id: description` when it doesn't.
- Roadmap entries use the format:
  `- [ ] \`[P1]\` \`[PREFIX-N]\` **Title** — description`
  Insert under the appropriate priority group; default to P1 if uncertain.
- Set new CC tasks to `pending` status; do not mark `in_progress`.
- Keep roadmap descriptions concise (≤ 1 line). Full detail belongs in a plan doc.
- After adding roadmap entries, commit the roadmap file immediately.
