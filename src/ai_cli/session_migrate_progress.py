"""Progress lines for the slow steps of a migration, so a long run is never silent.

A step is reported twice through the caller's ``progress`` callback: ``<what>: <doing>``
as it starts, then ``<what>: <outcome> (<elapsed>s)`` as it ends. ``None`` reports nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import monotonic

Progress = Callable[[str], None]


class Step:
    """The outcome a running step will report; ``"done"`` unless the step sets another."""

    def __init__(self) -> None:
        self.outcome = "done"


@contextmanager
def timed(progress: Progress | None, what: str, doing: str) -> Iterator[Step]:
    """Report ``what`` starting ``doing``, then its outcome and elapsed time when it ends.

    A step that raises reports ``failed after <elapsed>s`` and the exception goes on.
    """
    step = Step()
    if progress is None:
        yield step
        return
    progress(f"{what}: {doing}")
    started = monotonic()
    try:
        yield step
    except BaseException:
        progress(f"{what}: failed after {monotonic() - started:.1f}s")
        raise
    progress(f"{what}: {step.outcome} ({monotonic() - started:.1f}s)")
