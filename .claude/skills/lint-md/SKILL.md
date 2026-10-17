---
name: lint-md
description: Run the repository's pinned markdownlint-cli2 pre-commit hook on Markdown files.
---

# Markdown lint

Run markdown linting on demand through the pinned pre-commit hook. Do not use
`npx markdownlint-cli2`: it resolves packages at invocation time.

For the entire repository, run:

```bash
pre-commit run markdownlint-cli2 --all-files
```

For one or more specified files, run:

```bash
pre-commit run markdownlint-cli2 --files <file> [<file> ...]
```

The configured hook runs with `--fix`; report any remaining violations after
it completes.
