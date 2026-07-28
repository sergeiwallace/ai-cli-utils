"""Every shipped module must compile without SyntaxWarning.

``session_script.py`` embedded a shell regex (``\\.envrc``) inside a Python
f-string, where ``\\.`` is not a recognised escape.  Python currently warns and
falls back to the literal backslash, but this is slated to become a SyntaxError,
so it is a latent break rather than cosmetic noise -- and the warning printed on
stderr at every single session launch.

Compiling each file is checked instead of the one known line: the whole point is
to catch the next one, and a per-line assertion would not.
"""

import warnings
from pathlib import Path

import pytest

import ai_cli

_SOURCE_FILES = sorted(Path(ai_cli.__file__).parent.rglob("*.py"))


def test_given_package_when_collected_then_source_files_were_found():
    """Guard the guard: an empty glob would make every check below vacuous."""
    assert len(_SOURCE_FILES) > 10, f"expected the full package, found {len(_SOURCE_FILES)} files"


@pytest.mark.parametrize("source_file", _SOURCE_FILES, ids=lambda p: p.name)
def test_given_module_when_compiled_then_emits_no_syntax_warning(source_file):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile(source_file.read_text(encoding="utf-8"), str(source_file), "exec")

    offenders = [f"{w.lineno}: {w.message}" for w in caught if issubclass(w.category, SyntaxWarning)]
    assert not offenders, f"{source_file.name} emits SyntaxWarning(s): {offenders}"
