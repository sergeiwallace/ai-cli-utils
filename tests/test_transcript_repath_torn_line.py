"""One malformed JSONL line must not cost the whole transcript.

MEASURED, not hypothetical. During the SageMaker-to-EC2 migration this silently lost
three entire live-session transcripts and a subagents/ subtree:

    f5b6cafa-....jsonl    44 MB   (ai-cli-1's live session)
    f514cb58-....jsonl   142 MB   (kg-2's live session)
    6e58fe01-....jsonl   386 MB   (kg-1's live session) + 9 files under its subagents/

The reported cause was one line each:

    malformed JSONL at line 879: Expecting ',' delimiter: line 1 column 1000 (char 999)

Column 1000 of a 148 MB file is a torn append -- the snapshot caught the writer
mid-line. So the trigger is not corruption, it is copying a transcript that a live
session is still writing to, which during a migration is the normal case rather than
the exceptional one.

The old behaviour was deliberate and documented ("Malformed JSONL produces a loud error
and no destination copy for that file"), and that is exactly what makes it worth
changing: for a tool whose entire purpose is preserving history, discarding 148 MB to
protect the integrity of one line inverts the priority. A transcript missing one line
is readable and resumable; an absent transcript is neither.

Nothing here weakens the reporting. The line is still named in `errors` with its number
-- it is the DISCARD that goes away, not the diagnosis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_cli.transcript_repath import repath_project_dir

OLD_ROOT = "/old/base/projects"
NEW_ROOT = "/data/projects"


def _record(cwd: str, text: str) -> str:
    return json.dumps({"cwd": cwd, "type": "user", "message": {"content": text}}) + "\n"


@pytest.fixture
def torn_transcript(tmp_path: Path) -> tuple[Path, Path]:
    """A transcript whose middle line is truncated mid-JSON, as a torn append is."""
    src = tmp_path / "src-project"
    src.mkdir()
    good_before = _record(f"{OLD_ROOT}/repo", "before the tear")
    good_after = _record(f"{OLD_ROOT}/repo", "after the tear")
    # Truncated exactly the way a partial write leaves it: valid JSON prefix, no closer.
    torn = '{"cwd":"' + OLD_ROOT + '/repo","type":"user","message":{"content":"tor\n'
    (src / "session.jsonl").write_text(good_before + torn + good_after, encoding="utf-8")
    return src, tmp_path / "dst-project"


def test_a_torn_line_does_not_discard_the_transcript(
    torn_transcript: tuple[Path, Path],
) -> None:
    """The headline. Before the fix the destination file did not exist at all."""
    src, dst = torn_transcript

    repath_project_dir(src, dst, OLD_ROOT, NEW_ROOT)

    out = dst / "session.jsonl"
    assert out.is_file(), "one torn line discarded the whole transcript; this is how 386 MB was lost"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, f"every line must survive, got {len(lines)}: {lines}"


def test_the_parseable_lines_are_still_repathed(
    torn_transcript: tuple[Path, Path],
) -> None:
    """Salvage must not come at the cost of the rewrite the tool exists to do."""
    src, dst = torn_transcript

    repath_project_dir(src, dst, OLD_ROOT, NEW_ROOT)

    lines = (dst / "session.jsonl").read_text(encoding="utf-8").splitlines()
    for label, raw in (("first", lines[0]), ("last", lines[2])):
        record = json.loads(raw)
        assert record["cwd"] == f"{NEW_ROOT}/repo", f"{label} line was not repathed"


def test_the_torn_line_still_gets_its_path_rewritten(
    torn_transcript: tuple[Path, Path],
) -> None:
    """A salvaged line naming the OLD root would be a stale path we chose to keep.

    It cannot be rewritten through the JSON parser, so it gets a plain substring
    substitution. That is safe here precisely because the target is an absolute path
    prefix: it cannot straddle a JSON token boundary in a way that changes structure
    the line does not already have.
    """
    src, dst = torn_transcript

    repath_project_dir(src, dst, OLD_ROOT, NEW_ROOT)

    torn_out = (dst / "session.jsonl").read_text(encoding="utf-8").splitlines()[1]
    assert NEW_ROOT in torn_out
    assert OLD_ROOT not in torn_out, "the salvaged line kept the old root"
    # Still recognisably the same torn content, not silently repaired into valid JSON.
    assert torn_out.endswith("tor") or "tor" in torn_out
    with pytest.raises(json.JSONDecodeError):
        json.loads(torn_out)


def test_the_torn_line_is_reported_with_its_number(
    torn_transcript: tuple[Path, Path],
) -> None:
    """Salvaging must not become silence. The diagnosis was never the problem."""
    src, dst = torn_transcript

    result = repath_project_dir(src, dst, OLD_ROOT, NEW_ROOT)

    assert result.errors, "a torn line must still be reported"
    joined = " ".join(result.errors)
    assert "session.jsonl" in joined
    assert "2" in joined, f"the offending line number must be named: {result.errors}"


def test_the_count_of_unparsed_lines_is_available(
    torn_transcript: tuple[Path, Path],
) -> None:
    """A caller deciding whether a migration is acceptable needs the magnitude.

    One torn line in a 25,000-line transcript is a non-event; a thousand is a
    corrupt file. `errors` alone conflates them with per-file failures.
    """
    src, dst = torn_transcript

    result = repath_project_dir(src, dst, OLD_ROOT, NEW_ROOT)

    assert result.lines_unparsed == 1
    assert result.total_lines == 3
    # All THREE lines carried the old root and all three came out carrying the new one,
    # so all three are rewritten. This assertion originally said 2, on the assumption
    # that a salvaged line is not a rewritten one; that was wrong, and the counter is
    # the more useful of the two readings -- `lines_rewritten` answers "how many lines
    # changed", which a salvaged line did. `lines_unparsed` is the separate axis for
    # "how many could not be parsed", so the two together stay unambiguous.
    assert result.lines_rewritten == 3


def test_a_wholly_unreadable_file_still_reports_rather_than_pretending(
    tmp_path: Path,
) -> None:
    """The negative control: salvage must not turn a real failure green.

    A file that is not valid UTF-8 cannot be read as text at all, which is a different
    failure from a torn line and must stay loud.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "binary.jsonl").write_bytes(b"\xff\xfe\x00\x00 not utf-8 at all\n")

    result = repath_project_dir(src, tmp_path / "dst", OLD_ROOT, NEW_ROOT)

    assert result.errors, "an unreadable file must be reported"


def test_a_clean_transcript_is_untouched_by_the_salvage_path(tmp_path: Path) -> None:
    """Guard against the fix changing the ordinary case."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.jsonl").write_text(
        _record(f"{OLD_ROOT}/repo", "one") + _record(f"{OLD_ROOT}/repo", "two"),
        encoding="utf-8",
    )

    result = repath_project_dir(src, tmp_path / "dst", OLD_ROOT, NEW_ROOT)

    assert result.errors == []
    assert result.lines_unparsed == 0
    assert result.lines_rewritten == 2
    out = (tmp_path / "dst" / "clean.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(out) == 2
    assert all(json.loads(line)["cwd"] == f"{NEW_ROOT}/repo" for line in out)
