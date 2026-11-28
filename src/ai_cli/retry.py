"""One retry policy for every bounded wait: attempts, a growing per-attempt timeout, a jittered delay, a total.

A launch waits on things it does not control: an optional sidecar's unit manager, a name
registry's remote, an installer. Each used to carry its own fixed budget and gave up on the
first miss, so a host that was merely slow for a moment lost the step for the whole launch.
Every such wait now asks :func:`policy` for its site's numbers and runs through :func:`run`.

Prior art. The shape is tenacity's ``stop_after_attempt`` with ``wait_exponential_jitter``
(an initial wait, an exponential base, a cap and a jitter term) and the "exponential backoff
with jitter" pattern from the AWS Architecture Blog. tenacity itself is not a dependency: it
grows the *sleep* between attempts, while these waits also need the *timeout* of each attempt
to grow, which tenacity leaves to the wrapped function; and its policy is fixed when the
``Retrying`` object is built, where this one is read from config.toml on every call. What is
left is a frozen dataclass and one loop. ``max_total_seconds`` is tenacity's ``stop_after_delay``:
however many attempts the policy allows, the whole wait, delays included, ends inside it, and
each retry's timeout is cut to what remains. The first attempt always gets its full timeout,
so a total never makes a configured timeout lie: the total bounds the retries after it.

Configuration is the ``[retry]`` table of config.toml, read from the file on every call, never
captured at launch and never taken from an environment variable. ``[retry]`` holds the default
policy and ``[retry.<site>]`` overrides it for one site. Layers apply in this order, later
winning: the shipped ``[retry]``, the shipped ``[retry.<site>]``, the user's ``[retry]``, the
user's ``[retry.<site>]``. A key missing from the user's file therefore takes the shipped value.
The shipped values live in ``data/retry.toml``, never at a call site, and the config template
repeats them as commented lines: an active template value would be copied into every new
user's file and pin it there when the shipped default later changes.
"""

from __future__ import annotations

import functools
import math
import pkgutil
import random
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from . import config as _config
from . import output as _out
from .output import Tag

_T = TypeVar("_T")


@dataclass(frozen=True)
class RetryPolicy:
    """How often one site may try, how long each try may take, and how long it waits between."""

    attempts: int
    timeout_seconds: float
    growth: float
    max_timeout_seconds: float
    delay_seconds: float
    max_delay_seconds: float
    jitter: float
    max_total_seconds: float

    def timeout_for(self, attempt: int) -> float:
        """Seconds attempt ``attempt`` (1-based) may take: grown by ``growth`` per attempt, capped."""
        return min(self.timeout_seconds * self.growth ** (attempt - 1), self.max_timeout_seconds)

    def worst_case_seconds(self) -> float:
        """Seconds every attempt and every full, unjittered delay between them would take together."""
        timeouts = sum(self.timeout_for(n) for n in range(1, self.attempts + 1))
        return timeouts + sum(self.delay_after(n, rand=lambda: 0.0) for n in range(1, self.attempts))

    def delay_after(self, attempt: int, *, rand: Callable[[], float] = random.random) -> float:
        """Seconds to wait after failed attempt ``attempt``, less a random share of up to ``jitter`` of it."""
        delay = min(self.delay_seconds * self.growth ** (attempt - 1), self.max_delay_seconds)
        return delay * (1 - self.jitter * rand())


@dataclass(frozen=True)
class Attempt:
    """One try: its 1-based number, how many the policy allows, and its own timeout."""

    number: int
    of: int
    timeout: float


class RetryExhaustedError(Exception):
    """Every attempt failed transiently; ``last`` is the final cause."""

    def __init__(self, site: str, attempts: int, last: Exception, *, total: float | None = None) -> None:
        self.site = site
        self.attempts = attempts
        self.last = last
        self.total = total
        within = f" within its {total:g} s total" if total is not None else ""
        super().__init__(f"{last} after {attempts} attempt{'s' if attempts != 1 else ''}{within}")


def _positive(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value) and value > 0


def _non_negative(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value) and value >= 0


_VALID: dict[str, Callable[[object], bool]] = {
    "attempts": lambda value: not isinstance(value, bool) and isinstance(value, int) and value >= 1,
    "timeout_seconds": _positive,
    "growth": lambda value: _positive(value) and value >= 1,  # type: ignore[operator]
    "max_timeout_seconds": _positive,
    "delay_seconds": _non_negative,
    "max_delay_seconds": _non_negative,
    "jitter": lambda value: _non_negative(value) and value <= 1,  # type: ignore[operator]
    "max_total_seconds": _positive,
}
_RULES = {
    "attempts": "a whole number of at least 1",
    "growth": "a number of at least 1",
    "jitter": "a number from 0 to 1",
    "delay_seconds": "a number of seconds, 0 or more",
    "max_delay_seconds": "a number of seconds, 0 or more",
}


_SHIPPED_RESOURCE = "data/retry.toml"


@functools.cache
def shipped_table() -> dict:
    """The shipped ``[retry]`` table (``data/retry.toml``): the value of every key a user's file leaves out."""
    data = pkgutil.get_data("ai_cli", _SHIPPED_RESOURCE)
    if data is None:
        raise FileNotFoundError(f"ai_cli/{_SHIPPED_RESOURCE} is missing from the installed package")
    return tomllib.loads(data.decode("utf-8"))["retry"]


def _user_table() -> dict:
    path = Path(_config.config_file_text())
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        _out.warning(Tag.CONFIG, "load", f"cannot read [retry] from {path}: {exc}; using the shipped retry policy")
        return {}
    table = data.get("retry", {})
    if not isinstance(table, dict):
        _out.warning(Tag.CONFIG, "load", "retry must be a table; using the shipped retry policy")
        return {}
    return table


def _layer(table: dict, site: str) -> dict:
    section = table.get(site, {})
    return section if isinstance(section, dict) else {}


def policy(site: str) -> RetryPolicy:
    """``site``'s policy, read from config.toml now, every key validated and falling back to the shipped one."""
    shipped = shipped_table()
    values: dict[str, object] = {}
    for layer in (shipped, _layer(shipped, site)):
        values.update({key: layer[key] for key in _VALID if key in layer})
    missing = sorted(set(_VALID) - set(values))
    if missing:
        raise RuntimeError(f"the shipped [retry] table has no {', '.join(missing)}")
    user = _user_table()
    for name, layer in (("retry", user), (f"retry.{site}", _layer(user, site))):
        for key, valid in _VALID.items():
            if key not in layer:
                continue
            if valid(layer[key]):
                values[key] = layer[key]
            else:
                rule = _RULES.get(key, "a positive number of seconds")
                _out.warning(Tag.CONFIG, "load", f"invalid {name}.{key}: use {rule}; keeping {values[key]}")
    current = RetryPolicy(**values)  # type: ignore[arg-type]
    _warn_if_contradictory(site, current)
    return current


def _warn_if_contradictory(site: str, current: RetryPolicy) -> None:
    """Say so when the total leaves no room for a retry, or cannot admit every attempt it allows."""
    if current.attempts > 1 and current.timeout_seconds >= current.max_total_seconds:
        _out.warning(
            Tag.CONFIG,
            "load",
            f"retry.{site}: timeout_seconds ({current.timeout_seconds:g}) is at or above max_total_seconds "
            f"({current.max_total_seconds:g}), so no retry can run; raise max_total_seconds or lower "
            "timeout_seconds",
        )
    elif current.worst_case_seconds() > current.max_total_seconds:
        _out.warning(
            Tag.CONFIG,
            "load",
            f"retry.{site}: max_total_seconds ({current.max_total_seconds:g}) cannot admit all {current.attempts} "
            f"attempts, whose timeouts and delays need {current.worst_case_seconds():g} s; the later attempts get "
            "less time or none. Raise max_total_seconds, or lower attempts, timeout_seconds or growth",
        )


def run(
    site: str,
    call: Callable[[Attempt], _T],
    *,
    transient: Callable[[Exception], bool],
    on_retry: Callable[[Attempt, Exception, float], None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> _T:
    """``call`` under ``site``'s policy, read now.

    ``transient`` decides each failure: a transient one (a timeout, a lost connection) is
    retried with the next attempt's longer timeout; any other is re-raised at once, because a
    definite answer does not change by asking again. ``on_retry`` hears each retried failure
    with the next attempt's timeout, so the caller can say what it is doing. When the attempts
    or ``max_total_seconds`` run out, :class:`RetryExhaustedError` carries the count and the
    last cause, and names the total when that is what stopped it. The delay can overrun its
    estimate (a loaded host, a suspended clock), so an attempt with no time left is never
    called, and the caller only ever sees a real timeout.
    """
    current = policy(site)
    total = max(current.max_total_seconds, current.timeout_for(1))
    deadline = time.monotonic() + total
    last: Exception | None = None
    for number in range(1, current.attempts + 1):
        timeout = current.timeout_for(number)
        if last is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise RetryExhaustedError(site, number - 1, last, total=total) from last
        attempt = Attempt(number, current.attempts, timeout)
        try:
            return call(attempt)
        except Exception as exc:
            if not transient(exc):
                raise
            last = exc
            if number == current.attempts:
                raise RetryExhaustedError(site, number, exc) from exc
            delay = current.delay_after(number)
            left = deadline - time.monotonic() - delay
            if left <= 0:
                raise RetryExhaustedError(site, number, exc, total=total) from exc
            if on_retry is not None:
                on_retry(attempt, exc, min(current.timeout_for(number + 1), left))
            (sleep or time.sleep)(delay)
    raise AssertionError("unreachable: a policy has at least one attempt")
