---
name: tasks-panel
description: Re-display all open internal CC tasks for this session in the Claude Code TUI task panel.
---

# tasks

Re-display open CC tasks in the Claude Code TUI task panel. The TUI panel is ephemeral — it resets when a session exits — so this skill re-populates it from the persistent task database each time it's invoked.

**Usage:** `/tasks`

## Behavior

1. Call `TaskList` to retrieve all tasks from the persistent task database.
2. Filter to **pending** and **in_progress** tasks only.
3. Display the open tasks in the Claude Code TUI task panel.
4. Confirm to the user how many tasks were loaded into the panel.

## Notes

- If no open tasks exist, say so and suggest checking the roadmap.
- Completed tasks are excluded — only pending/in_progress tasks go into the panel.
- Does not modify task status — read-only from the task database perspective.
