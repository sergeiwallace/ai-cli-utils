---
name: implement
description: Full implementation workflow — branch, plan, approve, code, simplify, checks, UAT, PR
---

# implement

Full implementation workflow with human gates.

**Usage:** `/implement`


### 1. Create Feature Branch

```bash
git checkout main && git pull origin main
git checkout -b feature/short-description
```text

### 2. Present Implementation Plan

Generate a plan covering:
1. Files to create/modify
2. Key types and structures
3. Implementation order
4. Test strategy

Present the plan and **WAIT FOR APPROVAL**. Do not write any code until the user approves.

### 3. Implement

Write the code per the approved plan:
- Make atomic commits
- Run tests after each logical unit of work

### 4. Simplify (/simplify)

Run `/simplify` to review and fix:
- Unnecessary abstractions not in the spec
- Builder patterns where constructors suffice
- Verbose comments restating the code
- Dead code, unused imports, TODO placeholders
- Scope creep beyond the spec

### 5. Automated Checks

Run all checks — must ALL pass before UAT:



```bash
pytest
```json



### 6. UAT Presentation

Present the UAT summary and **WAIT FOR APPROVAL**. Do not create a PR until the user approves.

### 7. Create PR

```bash
git push -u origin <branch>
gh pr create --base main --head <branch> \
  --title "summary" --body "..."
```text

### 8. Report

Confirm: PR URL, branch name, files changed, test count.
