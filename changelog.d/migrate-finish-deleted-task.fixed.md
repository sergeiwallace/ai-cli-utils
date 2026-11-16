- `ai migrate-session OLD NEW --finish` no longer refuses every step when a task file the
  migration recorded has since been deleted. Before, a task id with neither its OLD nor its NEW
  file stopped the finish at preflight with "restore the missing artifact", which also skipped
  every other step. The task step writes NEW's file before it removes OLD's, so an interrupted
  move always leaves one of them. A task with neither file was deleted by the user, and it now
  counts as done. A task destination that differs from the recorded intent, or a source that
  changed after it, still refuses.
