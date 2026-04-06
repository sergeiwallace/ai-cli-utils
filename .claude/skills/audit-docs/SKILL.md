---
name: audit-docs
description: Crawl the codebase and verify all documentation is current, fix staleness
---

# audit-docs

Crawl the codebase and verify all documentation is current. Fix any staleness found.

**Usage:** `/audit-docs`

## Checks

Run these checks in order. For each mismatch, fix it directly (don't just report).

### 1. Directory README Indexes

For each directory under `docs/`:
- Read `docs/{dir}/README.md` (if it exists)
- List actual files in that directory
- Flag files present on disk but missing from the README
- Flag files in the README that no longer exist on disk

### 2. Test Count

If MEMORY.md or any roadmap doc references a test count, verify against actual:
- Run the project test suite with collection only to get a count
- Update if counts differ

### 3. Plan Doc Status

For each plan doc in `docs/plans/`:
- Check the status line
- Cross-reference with git log for recent activity
- Flag plans marked "In progress" with no recent commits
- Flag plans marked "Done" without evidence

### 4. Cross-References

Check that links between docs are valid:
- Internal markdown links point to files that exist
- External references (other project paths) are noted but not validated

## Output

Print a summary:
```
Doc Audit Results:
  [check] File structure: [result]
  [check] Test count: [result]
  [check] Plan status: [result]
  [check] Cross-references: [result]
```

Commit any fixes with message: `docs: audit-docs — fix stale {details}`
