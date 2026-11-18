---
title: "A launch can recreate an integer slot after migration"
category: bug
tags: [bug, sessions, migration, naming]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A launch can recreate an integer slot after migration

## Symptoms

After a migration to a human name, a request for the old integer title can start
creating artifacts under that title again. Index allocation can also choose
the freed slot for a new session.

## Reproduction Steps

Move a registered integer slot with `ai migrate-session OLD --to-name` and
then launch OLD. To reproduce the allocator case, migrate slot one and launch
with `ai c -I` and no positional name.

## Root Cause Analysis

The explicit index path built a fresh title when its tmux session or worktree
was gone. The next-free-index scan checked active sessions and worktrees but
didn't check which titles the repository's migration ledger had retired.

## Fix

An explicit old title follows the migration ledger to its existing destination.
If neither slot exists, launch refuses and prints the status command. New
index allocations skip recorded source titles in the selected repository.
Remote allocation and the command it hands off to agree on the final title.

## Verification

The CLI tests `test_given_a_migrated_integer_slot_when_requested_again_then_it_launches_the_renamed_slot`
and `test_given_slot_one_has_migrated_when_allocating_an_index_then_it_is_skipped`
failed on the unfixed code because launch returned the retired index. They
migrate fixture worktrees before exercising launch. The explicit case checks
both remote allocation and the subsequent launch with index naming enabled.

## Lessons Learned

An old slot's absence can mean it was migrated. Test both explicit and implicit
allocation with ledger evidence and a moved worktree.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
