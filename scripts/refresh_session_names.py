#!/usr/bin/env python3
"""Regenerate the shipped session-name seed under src/ai_cli/data/session-names/.

The seed is the list an install with no shared name registry picks session names
from. It is built by the same generator ``ai sync-names`` uses, at the package's
default policy (``ai_cli.name_list.DEFAULT_POLICY``): both pinned artifacts are
downloaded and checked against their digests, the static filters are applied, and
``names.txt`` and ``generation.json`` are rewritten. A failed download or digest
check exits non-zero and leaves both files unchanged.

Review the diff of ``names.txt`` before committing it. A token that is not a usable
first name goes into ``non-names.txt`` beside it, and the script is run again.

    uv run python scripts/refresh_session_names.py
"""

from __future__ import annotations

import sys

from ai_cli import name_list


def main() -> int:
    try:
        generation = name_list.generate(name_list.DEFAULT_POLICY)
    except name_list.NameListError as exc:
        sys.stderr.write(f"refresh failed: {exc}\n")
        return 1
    name_list.write_outputs(name_list.DATA_DIR, generation)
    free = name_list.one_edit_free_subset(generation.names)
    by_length = ", ".join(
        f"{length} letters: {count}" for length, count in generation.record["count_by_length"].items()
    )
    sys.stdout.write(
        f"wrote {generation.record['count']} names to {name_list.DATA_DIR} ({by_length}); "
        f"a one-edit-free subset holds at least {len(free)}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
