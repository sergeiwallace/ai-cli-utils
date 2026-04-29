
---
name: tasks
description: Display all open internal CC tasks for this session.
---

# tasks

Show all pending and in-progress CC tasks for the current session. Use at any point to see what's on the list, or invoke automatically at session start.

**Usage:** `/tasks`

## Behavior

1. Call `TaskList` to retrieve all tasks.
2. Display pending and in_progress tasks grouped by status, in order.
3. If no open tasks exist, say so and suggest checking the roadmap.

## Notes

- This is the on-demand version of the session-start task display.
- Does not modify any tasks — read-only.
- Completed and deleted tasks are not shown.
