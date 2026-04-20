# Autonomous Work Completion Format

> Standard format for presenting results after autonomous work sessions.
>
> Last updated: 2026-03-18

## The Problem

When Claude completes autonomous work (multiple tasks without human gates), the results presentation is inconsistent. Sometimes important issues are buried, follow-ups are missed, or the user can't quickly assess what happened. A standard format ensures fast decision-making when reviewing completed work.

## The Flow

```text
Autonomous work completes → Present UAT Summary → User reviews → Approve / Revise / Reject
```text

## Format

After completing autonomous work, always present results using this template:

```markdown
## UAT Summary

### What was built
- [1-3 bullet points summarizing deliverables]

### Files changed
- [list of files created/modified, grouped by category]

### Test results
- [test count, pass/fail, coverage if applicable]

### Issues found
- **Fixed:** [issues discovered and resolved during implementation]
- **Remaining:** [issues that need attention but weren't in scope]
- **Root causes noted:** [patterns identified for future prevention]

### Docs updated
- [list of docs that were updated as part of the work]
- [list of docs that need YOUR review before proceeding]

### Input / feedback needed
- [specific decisions or approvals needed from the user]

### Blockers to unlock
- [anything blocking further progress that requires human action]

### Next autonomous work options
- [2-3 options for what to work on next, with brief pros/cons]
- **Recommendation:** [which option and why]

### How to verify
- [steps the user can take to manually test/verify the work]

### Acceptance criteria status
- [x] Criterion 1
- [x] Criterion 2
- [ ] Criterion 3 (blocked by X)
```text

## Rules

1. **Always include Issues Found** — even if empty ("None found"). This is the most important section for trust.
2. **Root causes over workarounds** — if a bug was found, explain why it happened, not just that it was fixed.
3. **Separate fixed from remaining** — don't mix resolved issues with open ones.
4. **Docs needing review get their own callout** — don't bury "you need to review X" in a list.
5. **Next options always include a recommendation** — don't just list options, say what you'd do and why.

## Anti-Patterns

- **Trailing summary restating what was done:** The UAT format IS the summary. Don't add a paragraph after it saying the same thing.
- **Burying blockers:** If something needs human action, put it in Blockers, not buried in a bullet point.
- **Vague test results:** "Tests pass" is insufficient. Include count and any notable coverage gaps.
- **Missing acceptance criteria:** Every autonomous work session should map back to the spec/design doc.

## Cadence

| Activity | Frequency | Trigger |
|----------|-----------|---------|
| Present UAT Summary | After every autonomous work session | Completion of all tasks in scope |
| Present interim summary | After each major phase if session is long | Phase completion within autonomous work |
