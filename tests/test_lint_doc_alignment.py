"""Tests for scripts/lint_doc_alignment.py."""

from pathlib import Path

from scripts.lint_doc_alignment import check_alignment, extract_section


SHARED_HEADINGS = [
    "## Public Open-Source Package Standards",
    "## CLI Conventions",
    "## Test Requirements",
]


# ---------------------------------------------------------------------------
# extract_section
# ---------------------------------------------------------------------------


class TestExtractSection:
    def test_given_heading_present_when_extracted_then_returns_section_content(self, tmp_path):
        doc = tmp_path / "doc.md"
        doc.write_text("## Foo\n\nsome content\n\n## Bar\n\nother\n")
        text = doc.read_text()
        result = extract_section(text, "## Foo")
        assert "some content" in result
        assert "## Bar" not in result

    def test_given_heading_missing_when_extracted_then_returns_empty_string(self, tmp_path):
        doc = tmp_path / "doc.md"
        doc.write_text("## Other\n\ncontent\n")
        text = doc.read_text()
        result = extract_section(text, "## Missing")
        assert result == ""

    def test_given_last_section_when_extracted_then_returns_to_eof(self, tmp_path):
        doc = tmp_path / "doc.md"
        doc.write_text("## Only\n\nfinal content\n")
        text = doc.read_text()
        result = extract_section(text, "## Only")
        assert "final content" in result


# ---------------------------------------------------------------------------
# check_alignment — matching
# ---------------------------------------------------------------------------


MINIMAL_SHARED = (
    "## Public Open-Source Package Standards\n\n- No proprietary names\n\n"
    "## CLI Conventions\n\n- Use short and long flags\n\n"
    "## Test Requirements\n\n- Follow test naming conventions\n"
)


class TestCheckAlignmentMatch:
    def _write_pair(self, tmp_path, shared_sections: str):
        claude = tmp_path / "CLAUDE.md"
        gemini = tmp_path / "GEMINI.md"
        claude.write_text(f"# CLAUDE\n\n{shared_sections}\n\n## Other\n\nclaude-only\n")
        gemini.write_text(f"# GEMINI\n\n{shared_sections}\n\n## Role\n\ngemini-only\n")
        return claude, gemini

    def test_given_identical_sections_when_checked_then_returns_zero(self, tmp_path):
        claude, gemini = self._write_pair(tmp_path, MINIMAL_SHARED)
        assert check_alignment(claude, gemini) == 0

    def test_given_real_repo_files_when_checked_then_passes(self):
        repo_root = Path(__file__).parent.parent
        claude = repo_root / "CLAUDE.md"
        gemini = repo_root / "GEMINI.md"
        assert check_alignment(claude, gemini) == 0, (
            "CLAUDE.md and GEMINI.md shared sections are out of sync — run lint_doc_alignment.py"
        )


# ---------------------------------------------------------------------------
# check_alignment — drift detection
# ---------------------------------------------------------------------------


class TestCheckAlignmentDrift:
    def test_given_differing_section_content_when_checked_then_returns_one(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        gemini = tmp_path / "GEMINI.md"
        claude.write_text("## Public Open-Source Package Standards\n\n- No proprietary names\n- No personal IDs\n")
        gemini.write_text("## Public Open-Source Package Standards\n\n- No proprietary names\n")
        assert check_alignment(claude, gemini) == 1

    def test_given_section_missing_from_gemini_when_checked_then_returns_one(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        gemini = tmp_path / "GEMINI.md"
        claude.write_text("## CLI Conventions\n\n- Use short and long flags\n")
        gemini.write_text("# GEMINI\n\nNo CLI section here.\n")
        assert check_alignment(claude, gemini) == 1

    def test_given_section_missing_from_claude_when_checked_then_returns_one(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        gemini = tmp_path / "GEMINI.md"
        claude.write_text("# CLAUDE\n\nNo CLI section here.\n")
        gemini.write_text("## CLI Conventions\n\n- Use short and long flags\n")
        assert check_alignment(claude, gemini) == 1

    def test_given_pragma_gate_wording_differs_when_checked_then_returns_one(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        gemini = tmp_path / "GEMINI.md"
        claude.write_text(
            "## Test Requirements\n\n- **`# pragma: no cover` is a hard human gate** — never add it autonomously.\n"
        )
        gemini.write_text("## Test Requirements\n\n- **`# pragma: no cover`** — ask before adding.\n")
        assert check_alignment(claude, gemini) == 1
