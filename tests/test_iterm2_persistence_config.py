"""The shipped iTerm2 defaults and the ``[iterm2.persistence]`` loader."""

from pathlib import Path

from ai_cli.iterm2 import _DEFAULT_ITERM2_CONFIG

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_given_shipped_default_config_when_reference_copy_read_then_it_is_identical():
    """docs/reference/iterm2-defaults.toml documents the file `ai` writes on first use."""
    reference = (_REPO_ROOT / "docs" / "reference" / "iterm2-defaults.toml").read_text(encoding="utf-8")

    assert reference == _DEFAULT_ITERM2_CONFIG
