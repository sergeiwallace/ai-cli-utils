---
title: "UAT — iTerm2 Tab Title and Color System Redesign"
category: test
tags: [iterm2, uat, tab-title, tab-color, fleet, gemini]
status: pending
related_docs:
  - docs/designs/iterm2-title-color-system.md
  - docs/bugs/iterm2-title-color-system.md
---

# UAT — iTerm2 Tab Title and Color System Redesign

> **Date:** TBD
> **Status:** Pending
> **Task:** `[AI-CLI-18]`

## Table of Contents

- [Prerequisites](#prerequisites)
- [Unit Test Results](#unit-test-results)
- [UAT Test Cases](#uat-test-cases)
  - [TC-1: Profile Reload Verification](#tc-1-profile-reload-verification)
  - [TC-2: Bug 1 — Color Collision Fix](#tc-2-bug-1--color-collision-fix)
  - [TC-3: Bug 3 — Gemini Title Fix](#tc-3-bug-3--gemini-title-fix)
  - [TC-4: Bugs 4/5/6 — Gemini Wrong Directory Fix](#tc-4-bugs-456--gemini-wrong-directory-fix)
  - [TC-5: Bug 7 — Icon Variety](#tc-5-bug-7--icon-variety)
  - [TC-6: Bug 2 — Remote Session Icon (Deferred)](#tc-6-bug-2--remote-session-icon-deferred)
  - [TC-7: TOML Config Feature Flags](#tc-7-toml-config-feature-flags)
  - [TC-8: Custom Color in Palette](#tc-8-custom-color-in-palette)
- [Acceptance Criteria](#acceptance-criteria)
- [Feedback](#feedback)
- [Approval Log](#approval-log)

---

## Prerequisites

1. Deploy latest ai-cli-utils: `ai deploy` (already done as of 2026-04-02)
2. Reload iTerm2 Dynamic Profiles: **iTerm2 → Preferences → Profiles** — confirm `ClaudeCode-Coral`, `ClaudeCode-Purple`, `ClaudeCode-Gold`, `ClaudeCode-Cyan`, `ClaudeCode-Teal`, `ClaudeCode-Green`, `GeminiCLI-White`, `GeminiCLI-Navy`, `GeminiCLI-Gold` appear in the list. If not: `touch ~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-profiles.json`
3. On first `ai c` or `ai g` launch, `~/.config/ai-cli-utils/iterm2.toml` is auto-created — verify it exists after TC-2.

---

## Unit Test Results

```
768 passed, 2 skipped (2026-04-02)
49 new iTerm2 tests added:
  TestIsIterm2, TestLoadIterm2Config, TestIterm2Palette, TestIterm2ColorSchemes,
  TestAssignIterm2ColorSlot, TestReleaseIterm2ColorSlot, TestReleaseColorSlotCommand,
  TestGetEngineScriptIterm2Slot, TestLocalProjectChdir
```

---

## UAT Test Cases

### TC-1: Profile Reload Verification

**Steps:**
1. Open iTerm2 → Preferences → Profiles
2. Scroll the profile list

**Expected:** All new profiles visible: `ClaudeCode-Coral`, `ClaudeCode-White`, `ClaudeCode-Navy`, `ClaudeCode-Purple`, `ClaudeCode-Gold`, `ClaudeCode-Cyan`, `ClaudeCode-Teal`, `ClaudeCode-Green`, `GeminiCLI-White`, `GeminiCLI-Navy`, `GeminiCLI-Gold`

**Actual:**

**Result:** Pass / Fail

---

### TC-2: Bug 1 — Color Collision Fix

**Steps:**
1. Open a new tab in the ai-cli-utils project and run `ai c 2`
2. Open a new tab in another project and run `ai c 2`
3. Open a new tab in another project and run `ai c 2`
4. Observe the three tab background colors

**Expected:** All three tabs have distinct tab background colors (previously all got the same orange because session number "2" always mapped to slot 1)

**Actual:**

**Result:** Pass / Fail

---

### TC-3: Bug 3 — Gemini Title Fix

**Steps:**
1. Run `ai g 1` in any project
2. Observe the tab title and tab color

**Expected:**
- Tab title shows `✦ ▶ g-{project}-1` (not "Default")
- Tab gets a rolling color (not always static blue)
- Tab shows the Gemini logo icon (not a generic terminal icon)

**Actual:**

**Result:** Pass / Fail

---

### TC-4: Bugs 4/5/6 — Gemini Wrong Directory Fix

**Steps:**
1. From a project directory that is NOT the target project, run:
   ```
   ai g 1 -p myproject
   ```
2. Observe whether the session opens and whether it can resume

**Expected:** Session opens in another project directory; Gemini finds the correct chats directory; no "Invalid session identifier" error loop

**Actual:**

**Result:** Pass / Fail

---

### TC-5: Bug 7 — Icon Variety

**Steps:**
1. Open 4+ CC sessions across different projects (e.g. proj-a `c 1`, proj-b `c 1`, proj-c `c 1`, proj-d `c 1`)
2. Observe the tab icons

**Expected:** Different Claude logo color variants visible across tabs — at minimum a mix of coral, white, navy, and at least one of purple/gold/cyan/teal/green depending on slot assignment

**Actual:**

**Result:** Pass / Fail

---

### TC-6: Bug 2 — Remote Session Icon (Deferred)

**Steps:**
1. From any local session, run `ai c 1 -R` to open a remote mosh session
2. Observe the tab icon and tab color

**Expected:** Tab shows Claude logo (not terminal icon), tab gets a color (not grey), tab title shows `[mosh] ▶ c-r-{project}-1`

**Actual:**

**Notes:** This may still be broken due to `_ai_iterm2_precmd` race condition. If the terminal icon appears, the precmd is firing after the pre-launch emit and overriding the profile. Diagnosis: add `echo "precmd fired" >> /tmp/precmd-log.txt` to `_ai_iterm2_precmd` in `~/.zshrc` temporarily and check whether it fires during remote session launch.

**Result:** Pass / Fail / Deferred

---

### TC-7: TOML Config Feature Flags

**Steps:**
1. Open `~/.config/ai-cli-utils/iterm2.toml`
2. Set `show_type_symbol = false` under `[iterm2.tab_title]`
3. Launch a new `ai c` session
4. Observe the tab title

**Expected:** Tab title shows `▶ c-{project}-N` with no `*` prefix

**Actual:**

**Result:** Pass / Fail

---

### TC-8: Custom Color in Palette

**Steps:**
1. Open `~/.config/ai-cli-utils/iterm2.toml`
2. Under `[iterm2.palette]`, add: `magenta = "#c026d3"`
3. Under `[iterm2.color_schemes]`, add: `magenta = ["ClaudeCode-White", "GeminiCLI-White"]`
4. Launch several new `ai c` sessions until the magenta slot is assigned
5. Observe that one session gets a magenta tab

**Expected:** Custom color participates in the auto-rotation pool; session with magenta tab shows `ClaudeCode-White` (white icon on magenta background)

**Actual:**

**Result:** Pass / Fail

---

## Acceptance Criteria

- [ ] AC-1: Three concurrent sessions with same number suffix get distinct tab colors (Bug 1)
- [ ] AC-2: Gemini tab title shows session name, not "Default" (Bug 3)
- [ ] AC-3: Gemini tab gets dynamic rolling color, not static blue (Bug 3)
- [ ] AC-4: `ai g 1 -p PROJECT` works from any directory (Bugs 4/5/6)
- [ ] AC-5: 4+ concurrent CC sessions show visually distinct icon color variants (Bug 7)
- [ ] AC-6: `show_type_symbol = false` removes `*`/`✦` from tab title (TOML config)
- [ ] AC-7: Custom palette color participates in auto-rotation (TOML config)
- [ ] AC-8: Remote CC session shows Claude logo and tab color (Bug 2 — may be deferred)

---

## Feedback

### Iteration 1

[User feedback from first UAT pass. Never overwrite — add new iterations below.]

---

## Approval Log

| Date | Round | Decision | Notes |
|------|-------|----------|-------|
| | | | |
