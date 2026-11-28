# 0011. `setup.sh` constraints

Governs: `setup.sh`.

## Decision and reasons

- The vision prompt is read into a variable with `read -r -d ''` from a heredoc, not
  expanded inside `"$(cat <<...)"`. macOS's bash 3.2 reads an apostrophe in a heredoc inside
  `"$(...)"` as an open quote and fails to parse the rest of the script.
- The README commit never uses `--no-verify`. Pre-commit's fixing hooks (markdownlint,
  trailing-whitespace) can change `README.md` and fail the first attempt on purpose, so the
  script stages their fix and retries once before giving up. Rejected: bypassing the hooks.
- `uv sync --inexact` keeps compatible installed packages, such as extras, on a re-run.
- A failed hook install does not stop the script; see
  [0003](0003-hooks-install-through-pre-commit.md).
