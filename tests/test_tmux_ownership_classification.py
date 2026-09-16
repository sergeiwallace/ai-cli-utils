"""Classification of ``tmux new-session -P -F '#{session_id}'`` output.

Regression coverage for a launch failure that reported only "failed to establish
ownership": a tmux whose format expansion is not working echoes the format back
literally, so the launcher received ``#session_id`` where it required ``$0``. The
opaque-id regex rejects both that and a genuinely empty answer, but the two need
different messages -- a tmux that does not expand formats will not begin doing so
on retry, so the operator's remedy is to launch without tmux.
"""

from __future__ import annotations

import pytest

from ai_cli.tmux_ownership import classify_new_session_output


@pytest.mark.parametrize("raw", ["$0", "$3", "$42", "  $7  ", "$7\n"])
def test_given_valid_opaque_id_when_classified_then_ok(raw: str) -> None:
    status, value = classify_new_session_output(raw)
    assert status == "ok"
    assert value == raw.strip()


@pytest.mark.parametrize(
    "raw",
    [
        "#session_id",
        "#{session_id}",
        "#session_id\n",
        "#version",
    ],
)
def test_given_unexpanded_format_when_classified_then_reported_as_not_expanded(raw: str) -> None:
    """The defect this module exists to distinguish.

    ``#session_id`` is what a non-expanding tmux returns for ``#{session_id}``;
    ``#version`` is the same fault observed through a different format string.
    """
    status, value = classify_new_session_output(raw)
    assert status == "format-not-expanded"
    assert value == raw.strip()


@pytest.mark.parametrize("raw", ["", "   ", "\n", None, "garbage", "0", "$", "$abc", "12"])
def test_given_unusable_output_when_classified_then_reported_as_unusable(raw: str | None) -> None:
    status, _ = classify_new_session_output(raw)
    assert status == "unusable"


def test_given_each_class_when_classified_then_statuses_are_distinct() -> None:
    """Guards the whole point: the three classes must not collapse into one.

    A body replaced with a constant return, or one that dropped the format branch
    back into the generic failure path, passes every single-case assertion above
    while making the launcher's message wrong again.
    """
    assert classify_new_session_output("$1")[0] == "ok"
    assert classify_new_session_output("#session_id")[0] == "format-not-expanded"
    assert classify_new_session_output("")[0] == "unusable"
    statuses = {
        classify_new_session_output("$1")[0],
        classify_new_session_output("#session_id")[0],
        classify_new_session_output("")[0],
    }
    assert len(statuses) == 3
