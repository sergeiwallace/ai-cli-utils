"""Choosing a session name by hand: the ``-N/--name`` flow (AC-SM-1).

A hand-chosen name passes the same checks a picked one does, because a name one typo
from another session's, or one that reads as an ordinary word, cannot name one session
past doubt whoever chose it. The flow:

``start``
    A held or confusable name is refused with its reason. A known name inside the
    limits is reserved; a known name outside them shows the limits. An unknown name
    asks whether to add it. Without a terminal, each of these that needs an answer
    exits 2 instead, and an unknown name names ``ai add-name``.
``ask_add``
    ``y``/``yes`` adds the name (when it is inside the limits) and reserves it in one
    commit; ``n``/``no`` goes to the free-text question; anything else asks again.
``ask_free``
    ``auto`` picks a name. Any other value goes through the checks again; an unknown
    one inside the limits is added without a second question, because typing it here
    is the request to add it.
writing
    The reservation is pushed. A new tip that no longer allows a hand-chosen name
    sends the flow back to the free-text question with the reason. A picked name that
    cannot be published takes one of this machine's spares; a hand-chosen one never
    does, because a spare is not the name asked for.

End of input or Ctrl-C at any question exits 130 having written nothing.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from . import name_registry as reg
from . import session_naming

QUESTION = 'Name to use instead, or "auto" to pick one:'
EXIT_REFUSED = 2
EXIT_UNREACHABLE = 75
EXIT_ABORTED = 130


class PromptExit(Exception):
    """The flow ended without a session: ``code`` is the exit status, ``message`` why."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PromptIO:
    """How the flow talks to the person launching.

    ``ask`` returns the line typed, or None at end of input; Ctrl-C raises
    KeyboardInterrupt. ``say`` prints one message.
    """

    terminal: bool
    ask: Callable[[str], str | None]
    say: Callable[[str], None]


def choose_by_hand(
    registry: reg.Registry,
    slot: reg.Slot,
    value: str,
    *,
    io: PromptIO,
    words: frozenset[str],
    added_by: str,
    rng: random.Random | None = None,
) -> reg.Reservation:
    """Run the flow for ``--name value``; return the reservation it ends in, or raise :class:`PromptExit`."""
    try:
        registry.snapshot()
    except reg.RegistryUnreachableError as exc:
        raise PromptExit(
            EXIT_UNREACHABLE,
            f"{value.lower()} cannot be proved free because the registry is unreachable ({exc}); "
            "nothing was created and no spare was taken",
        ) from exc
    candidate: str | None = value.lower()
    from_free = False
    try:
        while True:
            if candidate is None:
                answer = io.ask(QUESTION)
                if answer is None:
                    raise PromptExit(EXIT_ABORTED, "end of input; nothing was created")
                candidate, from_free = answer.strip().lower(), True
            if from_free and candidate == "auto":
                return _write_picked(registry, slot, io, words, rng)
            decision = _decide(registry, slot, candidate, io, words, from_free=from_free)
            if decision is None:
                candidate = None
                continue
            name, add = decision
            try:
                return reg.reserve_hand_chosen(
                    registry, name, slot, add=add, via="launch-prompt", added_by=added_by, words=words
                )
            except reg.HandChosenRefusedError as exc:
                if not io.terminal:
                    raise PromptExit(EXIT_REFUSED, str(exc)) from exc
                io.say(str(exc))
                candidate = None
            except (reg.RegistryUnreachableError, reg.RegistryContendedError) as exc:
                raise PromptExit(
                    EXIT_UNREACHABLE,
                    f"the registry could not be written after {registry.attempts} attempts ({exc}); nothing was "
                    "created and no spare was taken, because a spare is not the name you chose",
                ) from exc
    except KeyboardInterrupt:
        raise PromptExit(EXIT_ABORTED, "cancelled; nothing was created") from None


def _decide(
    registry: reg.Registry,
    slot: reg.Slot,
    candidate: str,
    io: PromptIO,
    words: frozenset[str],
    *,
    from_free: bool,
) -> tuple[str, bool] | None:
    """``(name, add)`` to reserve, or None to ask the free-text question."""
    snapshot = registry.snapshot()
    verdict, reason = reg.check_hand_chosen(snapshot, candidate, slot, words)
    if verdict in ("held", "confusable", "word", "limits"):
        return _refuse(io, reason)
    if verdict == "ok":
        return candidate, False
    out_of_limits = reg.limits_reason(candidate, snapshot.policy)
    if from_free:
        return _refuse(io, out_of_limits) if out_of_limits else (candidate, True)
    if not io.terminal:
        raise PromptExit(
            EXIT_REFUSED,
            f"{candidate} is not in the name database; add it with `ai add-name {candidate}`, then launch again",
        )
    while True:
        answer = io.ask(f"{candidate} is not in the name database. Add it? [y/n]")
        if answer is None:
            raise PromptExit(EXIT_ABORTED, "end of input; nothing was created")
        answer = answer.strip().lower()
        if answer in ("y", "yes"):
            return _refuse(io, out_of_limits) if out_of_limits else (candidate, True)
        if answer in ("n", "no"):
            return None


def _refuse(io: PromptIO, reason: str) -> None:
    """Say why the value is refused, so the caller asks again; without a terminal, exit 2."""
    if not io.terminal:
        raise PromptExit(EXIT_REFUSED, reason)
    io.say(reason)


def _write_picked(
    registry: reg.Registry, slot: reg.Slot, io: PromptIO, words: frozenset[str], rng: random.Random | None
) -> reg.Reservation:
    try:
        return reg.reserve_picked(registry, slot, spool=reg.read_spool(), words=words, rng=rng)
    except session_naming.NamePoolExhaustedError as exc:
        raise PromptExit(1, str(exc)) from exc
    except (reg.RegistryUnreachableError, reg.RegistryContendedError):
        try:
            reservation = reg.take_spare(registry, slot)
        except reg.SparesExhaustedError as exc:
            raise PromptExit(EXIT_UNREACHABLE, str(exc)) from exc
        io.say(f"offline: using reserved spare {reservation.name}")
        return reservation
