"""Standing guard for the public-package naming rule (BUG-005).

``CLAUDE.md`` forbids private project names, personal identifiers, and
proprietary names anywhere in this repository's code, docs, comments and tests.
A one-off scrub enforced that once, in April 2026; with nothing standing behind
it, the same private project name walked back into four test files and one
docstring over the following months, unnoticed by review.

The scan is deliberately narrow in two ways so it stays trustworthy rather than
noisy:

* **Scope is ``src/`` and ``tests/``** — the shipped package and its suite. The
  ``docs/`` tree legitimately quotes real repository URLs and preserves
  historical records, so a docs-wide scan would cry wolf.
* **The pattern discriminates by role.** The forbidden token is also the first
  half of the project's real GitHub account name, which appears correctly in
  badge, CI, coverage and package-metadata URLs and in the author/copyright
  lines. Only the token *not* followed by the surname is a violation: every
  legitimate use is account-name or author-name usage and therefore carries it;
  no violation does. ``test_given_the_real_repository_url_...`` pins that
  distinction, and ``test_given_a_line_that_uses_...`` proves the scan can fail.
"""

from __future__ import annotations

import re
from pathlib import Path

_SCANNED_DIRS = ("src", "tests")

# Assembled from fragments so this guard file does not itself contain the
# literals it forbids. That keeps the scan free of self-exclusions, which would
# otherwise leave a hole in exactly the file most likely to carry them.
_PRIVATE_PROJECT_NAME = "ser" + "gei"
_PRIVATE_REPO_NAMES = ("bms-" + "semantic-knowledge-graph", "sw-" + "bms" + "-workspace")

# Proprietary platform names. ``CLAUDE.md`` forbids these alongside the personal
# identifiers above, but the pattern below originally carried only the personal
# half — so 19 platform-name uses sat in ``src/`` and ``tests/`` while all 12
# guards passed, honestly answering a narrower question than readers assumed.
#
# The second name was also a shipped config-section key, read by that key, so
# forbidding it required a rename of the section rather than a scrub. That rename
# has shipped, so the token is listed unconditionally here — no exemption list,
# which would have been the hole this guard exists to close.
_PRIVATE_PLATFORM_NAMES = ("ai" + "do", "ai-" + "core")

_FORBIDDEN = re.compile(
    "|".join(
        [
            # The private project name, except where it opens the real GitHub
            # account name (`<name>wallace`) or the author's full name.
            rf"{_PRIVATE_PROJECT_NAME}(?! ?wallace)",
            *(re.escape(name) for name in _PRIVATE_REPO_NAMES),
            # Word-bounded: the bare tool name is a substring of ordinary English
            # ("aid", "aiding") and of unrelated identifiers, so an unbounded
            # match would flag legitimate prose and make the guard noisy enough
            # to be disabled.
            *(rf"\b{re.escape(name)}\b" for name in _PRIVATE_PLATFORM_NAMES),
        ]
    ),
    re.IGNORECASE,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def scan_for_private_names(root: Path) -> list[str]:
    """Return ``path:lineno: line`` for every forbidden-name use under ``root``.

    Files that are not UTF-8 text (images, compiled artefacts) are skipped, so
    the scan needs no extension allowlist that a new file type could slip past.
    """
    findings: list[str] = []
    for scanned_dir in _SCANNED_DIRS:
        for path in sorted((root / scanned_dir).rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _FORBIDDEN.search(line):
                    findings.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
    return findings


def test_given_the_shipped_package_and_its_tests_when_scanned_then_no_private_names_remain():
    findings = scan_for_private_names(_repo_root())
    assert not findings, "private project names in a public package:\n" + "\n".join(findings)


def test_given_a_line_that_uses_the_private_name_as_a_project_name_when_scanned_then_it_is_flagged(tmp_path):
    """Positive control: the scan must go red on a real violation, not just pass."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "example.py").write_text(f'project_name = "{_PRIVATE_PROJECT_NAME}"\n')

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith("src/example.py:1:")


def test_given_the_real_repository_url_when_scanned_then_it_is_not_flagged(tmp_path):
    """Negative control: badge, metadata and author lines must not trip the scan.

    A naive substring search flags all of these, which is why "no hits" from one
    would be unreachable and "hits" uninformative.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "legitimate.py").write_text(
        f'URL = "https://github.com/{_PRIVATE_PROJECT_NAME}wallace/ai-cli-utils"\n'
        f'BADGE = "https://codecov.io/gh/{_PRIVATE_PROJECT_NAME}wallace/ai-cli-utils/graph/badge.svg"\n'
        f'AUTHOR = "{_PRIVATE_PROJECT_NAME.capitalize()} Wallace"\n'
        f'EMAIL = "dev@{_PRIVATE_PROJECT_NAME}wallace.com"\n'
    )

    assert scan_for_private_names(tmp_path) == []


def test_given_a_private_repository_name_when_scanned_then_it_is_flagged(tmp_path):
    """The private repo names are unconditional — they have no legitimate form here."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(f"# see {_PRIVATE_REPO_NAMES[0]} for context\n")

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith("tests/test_example.py:1:")


def test_given_a_private_platform_name_when_scanned_then_it_is_flagged(tmp_path):
    """Positive control per platform token: a guard nobody has watched fail enforces nothing.

    Parametrising over ``_PRIVATE_PLATFORM_NAMES`` rather than hardcoding one
    token means adding a name to that tuple without a working pattern fails here
    instead of passing silently. Each token gets its own file and its own
    assertion, so a token whose pattern never matches names itself in the failure
    rather than hiding inside an aggregate count.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for index, name in enumerate(_PRIVATE_PLATFORM_NAMES):
        (tmp_path / "src" / f"leak_{index}.py").write_text(f"# see {name} for context\n")

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == len(_PRIVATE_PLATFORM_NAMES)
    for index, name in enumerate(_PRIVATE_PLATFORM_NAMES):
        assert any(f.startswith(f"src/leak_{index}.py:1:") for f in findings), f"scan did not flag {name!r}"


def test_given_a_longer_identifier_containing_a_platform_name_when_scanned_then_it_is_not_flagged(tmp_path):
    """Negative control for the word boundary, using strings where it actually decides.

    Every string below matches the bare token WITHOUT ``\\b`` and none match with
    it, so removing the boundary turns this test red. An earlier version of this
    control used ordinary words ("aid", "aiding", "mermaid") and was **vacuous**:
    none of them contain the token at all, so it passed with the boundary removed
    and tested nothing. A control has to be able to fail for the reason it names.
    """
    token = _PRIVATE_PLATFORM_NAMES[0]
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "identifiers.py").write_text(f"{token}s = 1\nplaid{token} = 2\nmy{token}thing = 3\n")

    assert scan_for_private_names(tmp_path) == []


def test_given_a_non_utf8_file_when_scanned_then_it_is_skipped_without_error(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe binary")

    assert scan_for_private_names(tmp_path) == []
