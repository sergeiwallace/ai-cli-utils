"""Regression coverage for the shared quota statusline renderer."""

import re

import pytest

from ai_cli.quota import (
    CLAUDE_FIVE_HOUR_QUOTA,
    CLAUDE_WEEKLY_QUOTA,
    CODEX_WEEKLY_QUOTA,
    QuotaStatuslineSegment,
)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.mark.parametrize(
    ("adapter", "label"),
    [
        (CLAUDE_FIVE_HOUR_QUOTA, "cc5h"),
        (CLAUDE_WEEKLY_QUOTA, "ccWk"),
        (CODEX_WEEKLY_QUOTA, "cxWk"),
    ],
)
@pytest.mark.parametrize(
    ("delta", "expected_pace", "expected_color"),
    [
        (-5.0, "pace-5pp", "\x1b[38;2;48;160;178m"),
        (3.0, "pace+3pp", "\x1b[38;2;243;141;16m"),
        (15.0, "pace+15pp", "\x1b[38;2;220;38;38m"),
    ],
)
def test_given_pace_case_when_rendered_then_every_provider_uses_shared_signed_gradient(
    adapter, label, delta, expected_pace, expected_color
):
    now_epoch = 1_000_000.0
    reset_epoch = now_epoch + adapter.window_seconds / 2
    usage_pct = 50.0 + delta

    rendered = QuotaStatuslineSegment(adapter).render(usage_pct, reset_epoch, now_epoch, "↑")

    assert ANSI_ESCAPE.sub("", rendered) == f"{label} {usage_pct:.0f}% ↑ {expected_pace}"
    # The usage percentage and pace value intentionally use the same pace-derived color.
    assert rendered.count(expected_color) == 2


@pytest.mark.parametrize(
    ("delta", "expected"),
    [(-0.4, "-<1pp"), (0.4, "+<1pp"), (0.0, "±0pp")],
)
def test_given_sub_percentage_point_delta_when_formatted_then_direction_is_not_lost(delta, expected):
    assert QuotaStatuslineSegment.format_signed_delta(delta) == expected
