# 0002. Template-managed code holds the same bytes in every consumer

Governs: `scripts/check_ruff_version_sync.py`, `scripts/export_conversations.py`,
`scripts/session-broker.py`, `scripts/comment_ratchet.py`, `tests/test_comment_ratchet.py`,
`.githooks/`, and the ruff hooks in `.pre-commit-config.yaml`.

## Decision

Python files that project-template manages start with `# fmt: off`, and the consumer's ruff
hooks exclude `.githooks/`. Template scripts also run under the consumer's own `python3`,
which can be 3.10.

## Why

- Consumers run ruff at line lengths from 88 to 150. A template file that a consumer's
  formatter rewrites conflicts on every later copier update to that file. Rejected: shipping
  the files at one line length, which churns in every repository that uses another.
- Template-managed files should be fixed upstream in project-template, not linted into
  shape by each consumer.
- `scripts/session-broker.py` uses `timezone.utc`, not `datetime.UTC`, which needs Python
  3.11, because a consumer may run it under 3.10. Its `noqa: UP017` keeps ruff from
  rewriting it.
