"""Memory-safety regressions for Copier template inspection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.copier_update import _template_subdirectory


def test_given_non_text_git_output_when_reading_template_config_then_does_not_parse_stream(tmp_path: Path):
    """Unexpected subprocess output must not be consumed as an unbounded stream."""
    result = MagicMock(returncode=0)
    result.stdout.read.side_effect = AssertionError("non-text stdout was parsed as a stream")

    with patch("ai_cli.copier_update.subprocess.run", return_value=result):
        assert _template_subdirectory(tmp_path) == ""
