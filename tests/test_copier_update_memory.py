"""Memory-safety regressions for Copier template inspection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.copier_update import _template_config


def test_given_non_text_git_output_when_reading_template_config_then_does_not_parse_stream(tmp_path: Path):
    """Unexpected subprocess output must not be consumed as an unbounded stream.

    Targets ``_template_config``, which is where the ``git show`` call and its
    non-text-stdout guard live. This test used to call ``_template_subdirectory``,
    which owned both jobs; that function was split so it now takes an
    already-parsed mapping and runs no subprocess at all, leaving the test
    passing a path to a parameter expecting a dict.
    """
    result = MagicMock(returncode=0)
    result.stdout.read.side_effect = AssertionError("non-text stdout was parsed as a stream")

    with patch("ai_cli.copier_update.subprocess.run", return_value=result):
        assert _template_config(tmp_path) == {}
