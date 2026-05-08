---
name: clear-task-panel-done
description: Remove all completed tasks from the CC TUI task panel.
---

# clear-task-panel-done

Clears completed tasks from the CC TUI task panel by deleting them from the task database.

**Usage:** `/clear-task-panel-done`

## Behavior

1. Call `TaskList` to retrieve all tasks.
2. For each task with status `completed`, call `TaskUpdate` with `status: deleted` to remove it.
3. Report how many tasks were cleared.

## Notes

- Only removes `completed` tasks — pending and in_progress tasks are untouched.
- Deletion is permanent in the task database; use only when done tasks are no longer needed for reference.
