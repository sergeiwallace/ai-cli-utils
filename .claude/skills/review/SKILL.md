---
name: review
description: Cross-model code review — Gemini reviews Claude's diff for scope creep, dead code, naming, missed requirements
---

# review

Cross-model review of uncommitted changes. Gemini reviews Claude's code to catch blind spots that self-review misses.

**Usage:** `/review` or `/review --model gemini-2.5-pro`

## When to Use

- **Step 7 of the dev workflow** — always run before first ship (step 8)
- After any code changes, before committing
- On design/architecture docs before shipping

## How It Works

1. Collect the diff of uncommitted changes (`git diff` + `git diff --cached`)
2. Auto-select the Gemini reviewer model based on what changed (see tiers below)
3. Send the diff to Gemini with the review prompt
4. Present Gemini's findings to Claude
5. Claude acts on findings — fix issues, dismiss false positives, note for follow-up

## Review Tiers and Fallback Chains

Auto-determined by what files changed. No user input needed.

### Standard Tier (code changes)

**When:** Default for all code files

**Fallback chain:** `gemini-3-flash-preview` → `gemini-3.1-pro-preview` → `gemini-2.5-pro`

### Deep Tier (design/security)

**When:** Auto-escalated by detection rules:
- Files in `docs/designs/`, `docs/plans/`, `docs/vision/`
- Files matching `*auth*`, `*security*`, `*crypto*`, `*trading*`, `*migration*`
- DB schema changes (`*db*`, `*schema*`, `*migration*`)

**Fallback chain:** `gemini-3.1-pro-preview` → `gemini-3-flash-preview` → `gemini-2.5-pro`

## Fallback Behavior

1. Call the primary model for the detected tier
2. If the call fails (API 500, capacity error, quota exhaustion), fall to next in chain
3. If all models fail, report the failure — **never skip review**
4. Log which model was actually used in the output

## Override

`/review --model gemini-2.5-pro` — force a specific model, bypassing auto-selection.

## Review Prompt

Send this to Gemini along with the diff:

```
Review this code diff for a Python/TypeScript/Rust project. Check for:

1. **Scope creep** — changes beyond what was requested
2. **Dead code** — unused imports, unreachable branches, commented-out code
3. **Naming** — unclear variable/function names, inconsistent conventions
4. **Missed requirements** — anything the diff should address but doesn't
5. **Test quality** — tautological tests, missing edge cases, over-mocking
6. **Security** — input validation gaps, injection risks, exposed secrets
7. **AI slop** — unnecessary abstractions, builder patterns, verbose comments

For each finding, specify:
- Severity: CRITICAL / WARNING / SUGGESTION
- File and approximate location
- What's wrong and how to fix it

If the code looks good, say so briefly. Don't invent issues.
```

## Output Format

Present findings as:
```
## /review results (model: gemini-3-flash-preview)

### CRITICAL
- [file:line] Issue description — fix suggestion

### WARNING
- [file:line] Issue description — fix suggestion

### SUGGESTION
- [file:line] Issue description

### Verdict: PASS / NEEDS FIXES
```

## Rules

- Always run on uncommitted changes, not committed code
- Never skip review — if all models fail, report it
- Act on CRITICAL and WARNING findings before shipping
- SUGGESTION findings are optional — use judgment
- Don't re-review after `/simplify` — simplify is a separate cleanup pass
