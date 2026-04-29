---
name: jira
description: Jira operations — create issues, search, update, link, comment, sprint overview
---

# jira

Unified Jira operations skill. Routes to the right MCP call based on the subcommand.

**Usage:** `/jira <subcommand> [args]`

## Subcommands

### `/jira create <design doc path, epic description, or feature brief>`

Break down design docs, epics, or feature descriptions into structured Jira issues.

**Workflow:**

1. **Read context** — design doc, epic description, or feature brief
2. **Research** best approaches via Gemini CLI or web search if the topic warrants it
3. **Determine issue types** based on the input:
   - **Story** — user-facing feature work ("As a [role], I want [goal], so that [benefit]")
   - **Task** — technical work without direct user impact (refactors, infra, tooling, migrations)
   - **Bug** — defect fix (include reproduction steps, expected vs actual)
   - **Spike** — time-boxed research or investigation (include success criteria, not ACs)
4. **Generate issues** following `docs/procedures/jira-template.md` format:
   - Each issue has: Summary, Type, Objective, ACs (Given/When/Then for stories/tasks, repro steps for bugs, success criteria for spikes), Scope, Constraints, Test Strategy, Out of Scope
   - Include story points estimate (1/2/3/5/8/13)
   - Identify dependencies between issues
   - Order by dependency chain (independent first)
5. **Present for review** — show all generated issues in a summary table:

   | # | Type | Summary | Points | Dependencies |
   |---|------|---------|--------|-------------|
   | 1 | Story | ... | 3 | — |
   | 2 | Task | ... | 5 | Issue 1 |
   | 3 | Spike | ... | 2 | — |

   Then show full issue details below the table.

6. **Wait for approval** — do not create issues until the user confirms
7. **On approval**, create issues via `mcp__mcp-atlassian__jira_create_issue` with the correct `issuetype`
8. **Link relationships** — set parent epic, blocks/depends-on links
9. **Report** — list created issue keys with summaries

**Issue quality checklist** (verify before presenting):

- [ ] Has the correct issue type (Story/Task/Bug/Spike)
- [ ] Stories have a clear "As a [role], I want [goal], so that [benefit]"
- [ ] Bugs have reproduction steps and expected vs actual behavior
- [ ] Spikes have time-box and success criteria (not ACs)
- [ ] Has 2-8 ACs in Given/When/Then format (stories/tasks)
- [ ] Is sprint-sized (1-5 dev days, 1-13 story points)
- [ ] Is independent enough to be worked on without other issues in progress
- [ ] Has explicit Out of Scope section
- [ ] Scope lists specific files to create/modify
- [ ] Constraints reference relevant patterns from CLAUDE.md

**Epic generation:** When the input describes a large feature (5+ issues), also generate an Epic with summary, child issues, dependency order, and total story points.

### `/jira search <query or JQL>`

Search for issues by text or JQL query.

- If the query looks like JQL (contains `=`, `AND`, `ORDER BY`, etc.), pass it directly to `mcp__mcp-atlassian__jira_search`
- Otherwise, build a JQL text search: `text ~ "<query>" ORDER BY updated DESC`
- Display results in a table: Key, Type, Summary, Status, Assignee

### `/jira get <issue key>`

Fetch and display a single issue with all details.

- Use `mcp__mcp-atlassian__jira_get_issue`
- Show: Summary, Type, Status, Assignee, Description, ACs, Comments (last 5)

### `/jira update <issue key> <changes>`

Update issue fields or transition status.

- Parse natural language changes: "set status to In Progress", "assign to me", "add label backend", "set points to 5"
- For status changes, use `mcp__mcp-atlassian__jira_transition_issue` (fetch available transitions first via `mcp__mcp-atlassian__jira_get_transitions`)
- For field changes, use `mcp__mcp-atlassian__jira_update_issue`
- Confirm the change before applying

### `/jira comment <issue key> <text>`

Add a comment to an issue.

- Use `mcp__mcp-atlassian__jira_add_comment`
- If no text is provided, prompt for it

### `/jira link <source key> <link type> <target key>`

Create an issue link.

- Common link types: `blocks`, `is blocked by`, `relates to`, `duplicates`
- Use `mcp__mcp-atlassian__jira_update_issue` to add the link

### `/jira sprint`

Show current sprint overview.

- Use `mcp__mcp-atlassian__jira_get_agile_boards` to find the project board
- Use `mcp__mcp-atlassian__jira_get_board_issues` for current sprint issues
- Display: issue key, type, summary, status, assignee, points
- Show totals: total points, completed points, remaining

### `/jira board`

Show the project board with all columns.

- Use `mcp__mcp-atlassian__jira_get_agile_boards` + `mcp__mcp-atlassian__jira_get_board_issues`
- Group issues by status column

## No subcommand

If called without arguments (`/jira`), show the usage summary with all available subcommands.

## Rules

- Always confirm destructive or state-changing operations before applying
- Use the Jira project key from CLAUDE.md or aido.toml if available
- Format output as clean tables where possible
- For `/jira create`, never create issues without explicit user approval
