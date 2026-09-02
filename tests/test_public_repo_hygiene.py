"""Standing guard for the public-package naming rule (BUG-005).

``CLAUDE.md`` forbids private project names, personal identifiers, and
proprietary names in public code, metadata, and documentation. This guard
checks the shipped package, its tests, root metadata, project configuration,
and every documentation category. A line that must quote an identifier as
evidence may opt out with a documented per-line marker.
"""

from __future__ import annotations

import re
from pathlib import Path

_SCANNED_PATHS = ("src", "tests", "docs", "README.md", "CONTRIBUTING.md", "LICENSE", "pyproject.toml", ".github")
_LINE_EXEMPTION_MARKER = "public-hygiene: allow"
_EXEMPTION_PATHS = frozenset(
    {
        Path("README.md"),
        Path("CONTRIBUTING.md"),
        Path("pyproject.toml"),
        Path(".github"),
        Path("docs"),
        Path("tests") / "test_public_repo_hygiene.py",
    }
)

# Each literal below is intentionally exempted on its own line: the guard must
# scan the actual tokens it forbids, while its definitions and positive controls
# necessarily contain them. Other evidence quotes use the same per-line marker.
_PRIVATE_PROJECT_NAME = "sergeiwallace"  # public-hygiene: allow
_PERSONAL_IDENTIFIERS = (_PRIVATE_PROJECT_NAME, "sergei", "wallace")  # public-hygiene: allow
_PRIVATE_REPO_NAMES = ("bms-semantic-knowledge-graph", "sw-bms-workspace")  # public-hygiene: allow

# Proprietary platform names. ``CLAUDE.md`` forbids these alongside the personal
# identifiers above, but the pattern below originally carried only the personal
# half — so 19 platform-name uses sat in ``src/`` and ``tests/`` while all 12
# guards passed, honestly answering a narrower question than readers assumed.
#
# The second name was also a shipped config-section key, read by that key, so
# forbidding it required a rename of the section rather than a scrub. That rename
# has shipped, so the token is listed unconditionally here — no exemption list,
# which would have been the hole this guard exists to close.
_PRIVATE_PLATFORM_NAMES = ("aido", "ai-core")  # public-hygiene: allow

_FORBIDDEN = re.compile(
    "|".join(
        [
            *(rf"\b{re.escape(name)}\b" for name in _PERSONAL_IDENTIFIERS),
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


def _line_is_exempted(relative_path: Path, line: str) -> bool:
    """Allow documented evidence lines, but never let source code suppress the guard."""
    return _LINE_EXEMPTION_MARKER in line and any(
        relative_path == allowed or allowed in relative_path.parents for allowed in _EXEMPTION_PATHS
    )


def scan_for_private_names(root: Path) -> list[str]:
    """Return ``path:lineno: line`` for every forbidden-name use under ``root``.

    Files that are not UTF-8 text (images, compiled artefacts) are skipped, so
    the scan needs no extension allowlist that a new file type could slip past.
    """
    findings: list[str] = []
    for scanned_path in _SCANNED_PATHS:
        candidate = root / scanned_path
        if not candidate.exists():
            continue
        paths = candidate.rglob("*") if candidate.is_dir() else (candidate,)
        for path in sorted(paths):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                relative_path = path.relative_to(root)
                if _FORBIDDEN.search(line) and not _line_is_exempted(relative_path, line):
                    findings.append(f"{relative_path}:{lineno}: {line.strip()}")
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
    path, line, _ = findings[0].split(":", 2)
    assert Path(path) == Path("src") / "example.py"
    assert line == "1"


def test_given_root_metadata_with_a_personal_identifier_when_scanned_then_it_is_flagged(tmp_path):
    (tmp_path / "pyproject.toml").write_text(f'authors = [{{ name = "{_PERSONAL_IDENTIFIERS[0]}" }}]\n')

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    path, line, _ = findings[0].split(":", 2)
    assert Path(path) == Path("pyproject.toml")
    assert line == "1"


def test_given_a_private_repository_name_when_scanned_then_it_is_flagged(tmp_path):
    """The private repo names are unconditional — they have no legitimate form here."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(f"# see {_PRIVATE_REPO_NAMES[0]} for context\n")

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    path, line, _ = findings[0].split(":", 2)
    assert Path(path) == Path("tests") / "test_example.py"
    assert line == "1"


def test_given_a_historical_document_with_a_private_name_when_scanned_then_it_is_flagged(tmp_path):
    """Every documentation category is public and must remain within the guard's scope."""
    audit_dir = tmp_path / "docs" / "audits"
    audit_dir.mkdir(parents=True)
    (audit_dir / "example.md").write_text(f"project: {_PRIVATE_PROJECT_NAME}\n")

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    path, line, _ = findings[0].split(":", 2)
    assert Path(path) == Path("docs") / "audits" / "example.md"
    assert line == "1"


def test_given_an_explicitly_exempted_evidence_line_when_scanned_then_it_is_not_flagged(tmp_path):
    """The exemption is line-scoped, so neighboring violations are still visible."""
    docs_dir = tmp_path / "docs" / "audits"
    docs_dir.mkdir(parents=True)
    (docs_dir / "example.md").write_text(
        f"quoted evidence: {_PRIVATE_PROJECT_NAME} # {_LINE_EXEMPTION_MARKER}\n"
        f"unexempted violation: {_PRIVATE_PROJECT_NAME}\n"
    )

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith("docs/audits/example.md:2:")


def test_given_a_source_line_with_an_exemption_marker_when_scanned_then_it_is_still_flagged(tmp_path):
    """Only documented evidence locations may use the exemption marker."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "example.py").write_text(f'project_name = "{_PRIVATE_PROJECT_NAME}" # {_LINE_EXEMPTION_MARKER}\n')

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith("src/example.py:1:")


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
        assert any(
            Path(finding.split(":", 2)[0]) == Path("src") / f"leak_{index}.py" and finding.split(":", 2)[1] == "1"
            for finding in findings
        ), f"scan did not flag {name!r}"


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
