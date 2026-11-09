"""Split one test run across several CI jobs: ``pytest -p pytest_ci_shard --ci-shard=1/2``.

Each test goes to the shard picked by a CRC-32 of its node id, so every job running the
same tree selects the same tests without coordinating, every test lands in exactly one
shard, and tests of one heavy module are spread across shards rather than all landing in
one. The option exists only when this plugin is loaded, so a workflow that names the
option but forgets the plugin fails at startup instead of quietly running everything.

Why not pytest-split or pytest-test-groups: without a stored durations file both split
the items by their position in the collected order. pytest-randomly, a dev dependency
here, shuffles that order before any other plugin's hook runs, and each CI job draws its
own seed, so position-based shards would overlap and drop tests from job to job. A hash
of the node id does not depend on the order. pytest-shard does hash, but has had no
release since 2020.
"""

from __future__ import annotations

import re
import zlib
from typing import Any

import pytest

_SHARD_PATTERN = re.compile(r"^(\d+)/(\d+)$")


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--ci-shard",
        action="store",
        default=None,
        metavar="INDEX/COUNT",
        help="Run only shard INDEX of COUNT (1-based), selected by a stable hash of each test's node id.",
    )


def _parse_shard(value: str) -> tuple[int, int]:
    match = _SHARD_PATTERN.match(value)
    if match is None:
        raise pytest.UsageError(f"--ci-shard must look like INDEX/COUNT, for example 1/2; got {value!r}")
    index, count = int(match.group(1)), int(match.group(2))
    if count < 1 or not 1 <= index <= count:
        raise pytest.UsageError(f"--ci-shard index must be between 1 and the count; got {value!r}")
    return index, count


def shard_of(nodeid: str, count: int) -> int:
    """The 1-based shard a node id belongs to when the run is split ``count`` ways."""
    return zlib.crc32(nodeid.encode("utf-8")) % count + 1


def pytest_configure(config: Any) -> None:
    value = config.getoption("--ci-shard")
    if value is not None:
        _parse_shard(value)


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    value = config.getoption("--ci-shard")
    if value is None:
        return
    index, count = _parse_shard(value)
    selected = [item for item in items if shard_of(item.nodeid, count) == index]
    deselected = [item for item in items if shard_of(item.nodeid, count) != index]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
