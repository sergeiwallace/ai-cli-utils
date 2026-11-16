- `ai migrate-session OLD NEW --finish` no longer fails its Beads step when an issue the
  migration re-keyed has since been handed to another session. Before, an issue held by neither
  OLD nor NEW was reported as "no longer assigned to OLD or NEW; completion unverified", and the
  whole store counted as unreadable. That issue is no longer the migration's to re-key, so it
  now reads `already done`.
