"""The generated session script must parse under bash 3.2, macOS's /bin/bash.

bash 3.2 ends a ``$(...)``/``<(...)``/``>(...)`` substitution at the first
unparenthesized case-pattern ``)``, so a ``case`` written as ``pattern) ...`` inside
one makes the whole script a syntax error there, and the tmux pane dies empty.
bash 5 parses it fine, so Linux CI never sees it; the macOS Ctrl+Z test did, as
"the agent never started: ''". The leading-paren pattern form, ``(pattern) ...``,
is POSIX and parses under every bash, so the rule here is checkable on Linux:
every case clause in the rendered script uses it.
"""

from __future__ import annotations

import re

import pytest

from ai_cli.session_script import get_engine_script

_CASE_OPEN = re.compile(r"\bcase\b.+\bin\s*$")


def _bare_case_patterns(script: str) -> list[str]:
    bare: list[str] = []
    depth = 0
    for raw in script.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        if _CASE_OPEN.search(line):
            depth += 1
            continue
        if depth and re.match(r"esac\b", line):
            depth -= 1
            continue
        if depth and ")" in line and not line.startswith("("):
            bare.append(raw.strip())
    return bare


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
@pytest.mark.parametrize("launch_log", ["", "/tmp/launch.log"])
def test_given_a_rendered_session_script_when_scanned_then_every_case_pattern_is_parenthesized(engine, launch_log):
    script = get_engine_script(
        engine, "myproject-1", f"{engine}-myproject-1", f"{engine}-myproject-", "myproject", launch_log_path=launch_log
    )
    assert "esac" in script, "the scan found no case statement, so it proves nothing"
    assert _bare_case_patterns(script) == []


def test_given_a_bare_case_pattern_in_a_process_substitution_when_scanned_then_it_is_reported():
    script = (
        'exec 2> >(while read -r l; do\n  case "$l" in\n    "["*) printf x ;;\n    (*) printf y ;;\n  esac\ndone)\n'
    )
    assert _bare_case_patterns(script) == ['"["*) printf x ;;']
