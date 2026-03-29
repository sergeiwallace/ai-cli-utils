---
name: spend
description: Record ACTUAL Gemini API spend from billing dashboard (not estimates)
---

# spend

Record **actual** Gemini API spend from the billing dashboard. This is the authoritative number — distinct from `aido estimates` which shows token-based *estimates*.

**Usage:** `/spend 8.43` or `/spend $8.43`

## Dual-Tracking System

Two separate ledgers exist — don't confuse them:

1. **Actual billing** (`~/.aido/spend-reports.jsonl`): User-reported spend from Google billing dashboard. Authoritative.
   - `aido spend <amount>` — append to ledger
   - `aido spend` — view history
2. **Estimates** (`~/.aido/cost-estimates.jsonl`): Token-based approximations, appended automatically per API call.
   - `aido estimates` — show totals by model/thread

They diverge because free tier usage doesn't bill and Ultra pricing may differ from list rates.

## What to Do

1. Parse the amount from the user's input (strip `$` if present)
2. Run `aido spend <amount>` to record in persistent ledger
3. Update MEMORY.md "Current spend" line under "Gemini API Cost Tracking" with the new amount and today's date
4. Calculate remaining budget ($100 - spend)
5. Confirm the update

## Example

User: `/spend 8.43`

Action: Update MEMORY.md line to:
```
- **Current spend: $8.43 as of 2026-03-14** ($91.57 remaining, resets April 1)
```

Response: "Updated. $8.43/$100 — $91.57 remaining."

## Budget Planning

If spend is approaching $100, flag it and prefer:
- CLI models (free via subscription) over REST API
- Flash over Pro
- Fewer API-only runs

## Rules

- Always use today's date
- Budget is $100/month (Google AI Ultra), resets on the 1st
- Keep it to one line of confirmation — no narrative
- Always label estimates as "estimates" — never imply they're actual billing
