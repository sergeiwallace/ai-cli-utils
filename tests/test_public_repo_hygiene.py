"""Standing guard for the public-package naming rule (BUG-005).

``CLAUDE.md`` forbids private project names, personal identifiers, and
proprietary names in public code, metadata, and documentation. This guard
checks the shipped package, its tests, root metadata, project configuration,
and every documentation category. A line that must quote an identifier as
evidence may opt out with a documented per-line marker.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.publication_hygiene import (
    _PRIVATE_ENVIRONMENT_NAMES,
    _PRIVATE_ENVIRONMENT_PREFIX,
    _PRIVATE_TRACKER_ID,
    line_has_private_environment_name,
)

_SCANNED_PATHS = (
    "src",
    "tests",
    "docs",
    # ``scripts`` was omitted until AI-CLI-2jwm, and the omission was invisible
    # rather than documented: ``_EXPECTED_TOP_LEVEL_DIRS`` below lists "scripts",
    # so a grep for the name in this file finds a hit and suggests it is covered.
    # It is a different guard. Every file here is tracked and published, so a
    # private name in a maintenance script is as public as one in the package.
    "scripts",
    # Changelog fragments are published as CHANGELOG.md text once folded.
    "changelog.d",
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "pyproject.toml",
    ".github",
)

# Every top-level directory this repository legitimately tracks. Anything else
# appearing in the index is a generated or machine-specific artefact until proven
# otherwise -- see the guard that consumes this for why the allowlist is
# enumerated rather than the offenders being pattern-matched.
_EXPECTED_TOP_LEVEL_DIRS = frozenset(
    {
        ".beads",
        ".claude",
        ".githooks",
        ".github",
        ".vscode",
        "assets",
        # One changelog fragment per change, folded into CHANGELOG.md at release
        # (CONTRIBUTING.md, Changelog Fragments).
        "changelog.d",
        "config",
        "demo",
        "docs",
        "scripts",
        "src",
        "tests",
    }
)
_LINE_EXEMPTION_MARKER = "public-hygiene: allow"
_EXEMPTION_PATHS = frozenset(
    {
        Path("README.md"),
        Path("CONTRIBUTING.md"),
        Path("pyproject.toml"),
        Path(".github"),
        Path("docs"),
        Path("scripts") / "publication_hygiene.py",
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

# Private machine and client identifiers. These are SSH-host information in the
# sense that matters here: a name that identifies one real machine, or the
# organization whose network it sits on. They reached ``src/`` and ``tests/`` as
# honest provenance notes ("Measured on <machine>: ..."), which is exactly how
# this class arrives -- nobody pastes a hostname on purpose. The measurement is
# worth keeping; the name is not, and "an EC2 Linux host" carries the same
# information to a reader who does not have the machine.
_PRIVATE_MACHINE_NAMES = ("sem-kg", "bms")  # public-hygiene: allow

# The personal session prefix. Session, pane and worktree names are built from a
# two-letter prefix -- ``<prefix>-1``, ``c-<prefix>-1``, ``.worktrees/<prefix>-3``
# -- and those two letters are one operator's initials. So every such name is a
# personal identifier wearing an innocuous shape, which is how 750 of them sat in
# a public package while every guard above passed: none of them was looking for a
# name that reads like a generated slug.
#
# Two patterns rather than one entry in _FORBIDDEN, because the bare token cannot
# be forbidden outright. A word-bounded case-insensitive match on it hits a
# third-party documentation URL (the Kitty terminal's ``sw.kovidgoyal.net``) and  # public-hygiene: allow
# an unrelated two-letter column abbreviation in a design table -- so a single
# rule would be noisy enough to be switched off, which is the failure the
# platform-name comment above describes. These two forms are the ones that
# actually carry the prefix, and neither can match prose or a hostname.
_PRIVATE_SESSION_PREFIX = "sw"  # public-hygiene: allow

# Form 1: the prefix followed by a separator, anywhere a name is built. Nothing
# is required *after* the dash, and that is load-bearing rather than lax: the
# prefix also ships as a bare 51 times in the argument form ``"c-<prefix>-"``,
# where the name is completed by the callee, and as a format placeholder
# (``<prefix>-{n}``, ``<prefix>-*``). A rule requiring a trailing alphanumeric
# reads as the obvious one and misses all 80 of those.
#
# Deliberately case-SENSITIVE, which is the one boundary in this file drawn by
# case rather than by shape. The uppercase form ``SW-1234`` is not a session name  # public-hygiene: allow
# at all: it is a private dev-tracking issue id, and it belongs to a class this
# repository also carries 149 instances of under a *different* prefix
# (``AIH-1234``). Folding the uppercase form in here would produce a guard that  # public-hygiene: allow
# forbids one private tracker's ids and silently permits the other's -- an
# incoherent rule that reads as complete. That class is tracked as its own issue.
_SESSION_NAME = re.compile(rf"(?:^|[^A-Za-z0-9]){re.escape(_PRIVATE_SESSION_PREFIX)}-")

# Form 2: the prefix as a quoted string literal -- a config value or a test
# argument (``task_prefix = "sw"``). Case-insensitive is safe here where it was  # public-hygiene: allow
# not above: a bare two-character quoted string is never prose, never a URL, and
# never a tracker id, so this rule has no false-positive surface to speak of.
_SESSION_PREFIX_LITERAL = re.compile(rf"[\"']{re.escape(_PRIVATE_SESSION_PREFIX)}[\"']", re.IGNORECASE)

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
            # Bounded on the LEFT only. These are prefixes of longer host names
            # (``bms-windows``, ``sem-kg-ec2``), so a right boundary would match  # public-hygiene: allow
            # only the bare stem and miss every real hostname built from it --
            # which is the entire class.
            *(rf"\b{re.escape(name)}" for name in _PRIVATE_MACHINE_NAMES),
        ]
    ),
    re.IGNORECASE,
)

# Literal IPv4 addresses this repository may contain. Every one is synthetic: the
# RFC 5737 documentation range is matched by rule below, and these are the
# pre-existing placeholders (a fake public address, the CGNAT network base
# address a VPN range is illustrated with, RFC 1918 addresses inside a fabricated
# ``ifconfig`` output, loopback and the unspecified address).
#
# Enumerating what is ALLOWED rather than pattern-matching what is forbidden is
# deliberate, and it is the same inversion ``_EXPECTED_TOP_LEVEL_DIRS`` uses: a
# reserved-range rule looks principled and is not, because both errors occur in
# practice. A real machine's address inside the CGNAT range -- which is where a
# VPN puts every host -- would pass such a rule, and that is precisely the leak
# this guard was added for; meanwhile ``100.0.0.1``, a placeholder, is globally
# routable and would be flagged. So a new address is a deliberate one-line
# decision, and new placeholders should use ``192.0.2.x`` and need no entry.
_ALLOWED_IP_LITERALS = frozenset(
    {"1.2.3.4", "100.64.0.1", "100.0.0.1", "10.64.0.1", "192.168.1.5", "127.0.0.1", "0.0.0.0"}
)
# RFC 5737 TEST-NET-1/2/3, reserved for documentation and guaranteed to route nowhere.
_DOCUMENTATION_IP_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")
# All four octets bounded to 0-255, so a dotted version string ("2.1.220.300")
# is not mistaken for an address. Anchored on non-digits rather than \b, because
# \b does not separate a digit from a preceding dot.
_IPV4 = re.compile(
    r"(?<![\d.])((?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d))(?![\d.])"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _line_has_forbidden_name(line: str) -> bool:
    """True if ``line`` carries any forbidden name, in any of the three patterns."""
    return bool(_FORBIDDEN.search(line) or _SESSION_NAME.search(line) or _SESSION_PREFIX_LITERAL.search(line))


def _line_is_exempted(relative_path: Path, line: str) -> bool:
    """Allow documented evidence lines, but never let source code suppress the guard."""
    return _LINE_EXEMPTION_MARKER in line and any(
        relative_path == allowed or allowed in relative_path.parents for allowed in _EXEMPTION_PATHS
    )


def _scanned_text_files(
    root: Path, scanned_paths: tuple[str, ...] = _SCANNED_PATHS
) -> Iterator[tuple[Path, Path, str]]:
    """Yield ``(path, path relative to root, text)`` for every scanned text file.

    Files that are not UTF-8 text (images, compiled artefacts) are skipped, so
    the scan needs no extension allowlist that a new file type could slip past.

    ``scanned_paths`` is a parameter because the tracker-id guard below covers a
    deliberately narrower set than the name guard, and defaults to the full set so
    every existing caller is unchanged.
    """
    tracked: set[str] | None = None
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            tracked = {path for path in result.stdout.decode("utf-8").split("\0") if path}
    for scanned_path in scanned_paths:
        candidate = root / scanned_path
        if not candidate.exists():
            continue
        paths = candidate.rglob("*") if candidate.is_dir() else (candidate,)
        for path in sorted(paths):
            if not path.is_file():
                continue
            relative_path = path.relative_to(root)
            if tracked is not None and relative_path.as_posix() not in tracked:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            yield path, relative_path, text


def scan_for_private_names(root: Path) -> list[str]:
    """Return ``path:lineno: line`` for every forbidden-name use under ``root``.

    The path is rendered with ``as_posix()`` so a finding reads the same on every
    OS. Interpolating the ``Path`` directly gives ``src\\example.py`` on Windows,
    and every caller that matches a documented ``src/example.py:1:`` prefix then
    silently stops matching -- reporting "scan did not flag" for a name the scan
    did in fact flag.
    """
    findings: list[str] = []
    for path, relative_path, text in _scanned_text_files(root):
        del path
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _line_has_forbidden_name(line) and not _line_is_exempted(relative_path, line):
                findings.append(f"{relative_path.as_posix()}:{lineno}: {line.strip()}")
    return findings


def test_given_the_shipped_package_and_its_tests_when_scanned_then_no_private_names_remain():
    findings = scan_for_private_names(_repo_root())
    assert not findings, "private project names in a public package:\n" + "\n".join(findings)


# The private environment repository this package was first written against, and the
# names its sessions carry: the repository itself, the chief session's name segment, and
# the repository's two-letter-plus session prefix (``<prefix>-1``, ``c-r-<prefix>-box-1``,
# ``branch.<b>.<prefix>-session-home``). They sat in ``src/`` as load-bearing values -- the
# project a chief launches into, its registry key, git-config marker keys -- so forbidding
# them needed those values moved into configuration first, not a scrub (AI-CLI-nout).
# Everything the name guard scans except ``docs``. Measured 2026-10-21 with `grep -rniE`:
# ``docs`` holds about 150 lines naming the repository and about 200 carrying the prefix,
# across design records, audits and the archived roadmap, where a name is often the only
# provenance a maintainer has. Rewriting those is its own change; the shipped package, its
# tests, scripts and the root metadata a reader sees first are this guard's scope.
_ENVIRONMENT_NAME_SCANNED_PATHS = tuple(path for path in _SCANNED_PATHS if path != "docs")


def _line_has_environment_name(line: str) -> bool:
    return line_has_private_environment_name(line)


def scan_for_private_environment_names(root: Path) -> list[str]:
    """Return ``path:lineno: line`` for every private environment name outside ``docs``."""
    findings: list[str] = []
    for path, relative_path, text in _scanned_text_files(root, _ENVIRONMENT_NAME_SCANNED_PATHS):
        del path
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _line_has_environment_name(line) and not _line_is_exempted(relative_path, line):
                findings.append(f"{relative_path.as_posix()}:{lineno}: {line.strip()}")
    return findings


def test_given_the_shipped_package_and_its_tests_when_scanned_then_no_private_environment_names_remain():
    findings = scan_for_private_environment_names(_repo_root())
    assert not findings, "private environment names in a public package:\n" + "\n".join(findings)


# Public design and audit documents may describe provenance without publishing the
# private orchestration carrier that produced them: workflow metadata keys, tracker ids,
# environment names, or the session directory a run was driven from.
_PUBLIC_PROVENANCE_DOC_DIRS = ("docs/audits", "docs/designs")
_PRIVATE_WORKFLOW_METADATA = re.compile(
    r"\b(?:canonical_root_json|founding_ask_ref_sha256|task_id)\b|^\*\*Run Ledger:\*\*",
    re.MULTILINE,
)

# A delegated run's worktree lives at ``.worktrees/<session>-agents/<run>``, and the
# session segment is a fleet session name (environment prefix plus host alias). A
# placeholder in angle brackets, alone or dash-joined, carries no name.
_WORKTREE_SESSION_DIR = re.compile(r"\.worktrees/(?P<name>[^/\s`'\"]+)-agents\b")
_WORKTREE_SESSION_PLACEHOLDER = re.compile(r"<[^<>/]+>(?:-<[^<>/]+>)*")

# Files already on main that carry historical ids, measured 2026-11-11 against the
# patterns below: 15 of the 29 Markdown files in these folders. They are skipped unless
# a branch changes them, so a pre-existing provenance note does not turn the gate red; a
# branch that edits one must scrub it. A test below fails once an entry is clean, so a
# scrubbed file has to leave the set.
_PROVENANCE_DOC_BASELINE = frozenset(
    {
        Path("docs/designs/iterm2-cross-machine-vscode-routing.md"),
        Path("docs/designs/user-behavior-telemetry.md"),
        Path("docs/designs/vscode-terminal-tab-naming.md"),
    }
)


def _line_has_worktree_session_name(line: str) -> bool:
    return any(
        match["name"] != "agents" and not _WORKTREE_SESSION_PLACEHOLDER.fullmatch(match["name"])
        for match in _WORKTREE_SESSION_DIR.finditer(line)
    )


def _line_has_private_workflow_metadata(line: str) -> bool:
    return bool(
        _line_has_environment_name(line)
        or _PRIVATE_TRACKER_ID.search(line)
        or _PRIVATE_WORKFLOW_METADATA.search(line)
        or _line_has_worktree_session_name(line)
    )


def _git_stdout(root: Path, *args: str) -> str | None:
    """Stdout of a git command in ``root``, or None when git or the command fails.

    Every caller diffs against ``origin/main``, which fails where that ref is absent. CI
    checks out one commit with no remote-tracking refs (``actions/checkout`` defaults to
    ``fetch-depth: 1``), so the branch diff is a local-only signal and the full-folder
    scan is what CI runs.
    """
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _branch_changed_provenance_docs(root: Path) -> set[Path]:
    """Markdown files under the provenance dirs that differ from ``origin/main``."""
    stdout = _git_stdout(root, "diff", "--name-only", "origin/main...HEAD", "--", *_PUBLIC_PROVENANCE_DOC_DIRS)
    if stdout is None:
        return set()
    return {Path(name) for name in stdout.splitlines() if name.endswith(".md") and (root / name).is_file()}


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _branch_added_lines(root: Path, relative_path: Path) -> list[tuple[int, str]]:
    """``(lineno, line)`` for each line the branch added to ``relative_path``."""
    stdout = _git_stdout(
        root, "diff", "--no-color", "--no-ext-diff", "-U0", "origin/main...HEAD", "--", relative_path.as_posix()
    )
    added: list[tuple[int, str]] = []
    lineno = 0
    for diff_line in (stdout or "").splitlines():
        header = _HUNK_HEADER.match(diff_line)
        if header:
            lineno = int(header[1])
        elif diff_line.startswith("+") and not diff_line.startswith("+++"):
            added.append((lineno, diff_line[1:]))
            lineno += 1
    return added


def _public_provenance_scan_targets(root: Path) -> list[tuple[Path, list[tuple[int, str]]]]:
    """Each in-scope Markdown file in the provenance dirs, with the lines to scan in it.

    The brief for this guard asked for the changed set with an all-files fallback. A plain
    all-files fallback cannot be used: CI never has ``origin/main``, and the historical
    files on main would turn it red. A changed-only scan cannot stand alone either: on main
    itself, or in CI, it scans nothing and passes vacuously. So every file outside the
    baseline is scanned in full everywhere, and a baseline file the branch changed is
    scanned on the lines the branch added, so fixing one part of a historical document
    does not make its old ids the editor's problem. Without ``origin/main`` baseline files
    stay skipped.
    """
    tracked_output = _git_stdout(root, "ls-files", "-z")
    tracked = None if tracked_output is None else {path for path in tracked_output.split("\0") if path}
    every_doc = {
        path.relative_to(root)
        for directory in _PUBLIC_PROVENANCE_DOC_DIRS
        if (root / directory).is_dir()
        for path in (root / directory).rglob("*.md")
        if path.is_file() and (tracked is None or path.relative_to(root).as_posix() in tracked)
    }
    changed = _branch_changed_provenance_docs(root)
    targets: list[tuple[Path, list[tuple[int, str]]]] = []
    for relative_path in sorted(every_doc | changed):
        if relative_path not in _PROVENANCE_DOC_BASELINE:
            text = (root / relative_path).read_text(encoding="utf-8")
            targets.append((relative_path, list(enumerate(text.splitlines(), start=1))))
        elif relative_path in changed:
            targets.append((relative_path, _branch_added_lines(root, relative_path)))
    return targets


def scan_public_docs_for_private_workflow_metadata(root: Path) -> list[str]:
    """Return design and audit lines that expose private workflow provenance."""
    findings: list[str] = []
    for relative_path, lines in _public_provenance_scan_targets(root):
        for lineno, line in lines:
            if _line_has_private_workflow_metadata(line) and not _line_is_exempted(relative_path, line):
                findings.append(f"{relative_path.as_posix()}:{lineno}: {line.strip()}")
    return findings


def test_given_public_design_and_audit_docs_when_scanned_then_private_workflow_metadata_is_absent():
    scanned = _public_provenance_scan_targets(_repo_root())
    findings = scan_public_docs_for_private_workflow_metadata(_repo_root())

    assert scanned, "no design or audit document was in scope, so the scan measured nothing"
    assert not findings, "private workflow metadata in public design docs:\n" + "\n".join(findings)


def test_given_the_provenance_baseline_when_read_then_every_entry_still_carries_historical_metadata():
    root = _repo_root()
    clean = [
        path.as_posix()
        for path in sorted(_PROVENANCE_DOC_BASELINE)
        if not (root / path).is_file()
        or not any(
            _line_has_private_workflow_metadata(line) and not _line_is_exempted(path, line)
            for line in (root / path).read_text(encoding="utf-8").splitlines()
        )
    ]

    assert not clean, f"baseline entries that are missing or already clean, remove them: {clean}"


def test_given_private_workflow_metadata_in_a_public_design_when_scanned_then_it_is_flagged(tmp_path):
    design = tmp_path / "docs" / "designs" / "example.md"
    design.parent.mkdir(parents=True)
    design.write_text('task_id = "private-task"\n', encoding="utf-8")

    findings = scan_public_docs_for_private_workflow_metadata(tmp_path)

    assert findings == ['docs/designs/example.md:1: task_id = "private-task"']


# The first name is built from the environment prefix, the shape the leak shipped in; the
# second carries no other forbidden token, so it proves the worktree rule on its own.
@pytest.mark.parametrize("session", [f"{_PRIVATE_ENVIRONMENT_PREFIX}-host-1", "example-host-1"])
def test_given_a_ledger_line_naming_a_session_worktree_when_scanned_then_it_is_flagged(tmp_path, session):
    line = f"| Orchestrator worktree | `.worktrees/{session}-agents/example-run` |"
    ledger = tmp_path / "docs" / "audits" / "example-run-ledger.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(f"{line}\n", encoding="utf-8")

    findings = scan_public_docs_for_private_workflow_metadata(tmp_path)

    assert findings == [f"docs/audits/example-run-ledger.md:1: {line}"]


def test_given_a_tracker_id_in_an_audit_doc_when_scanned_then_it_is_flagged(tmp_path):
    line = f"<!-- template from {_PRIVATE_ENVIRONMENT_PREFIX.upper()}-52 -->"
    audit = tmp_path / "docs" / "audits" / "example-audit.md"
    audit.parent.mkdir(parents=True)
    audit.write_text(f"{line}\n", encoding="utf-8")

    findings = scan_public_docs_for_private_workflow_metadata(tmp_path)

    assert findings == [f"docs/audits/example-audit.md:1: {line}"]


def test_given_placeholder_worktree_paths_when_scanned_then_they_are_not_flagged(tmp_path):
    audit = tmp_path / "docs" / "audits" / "example-run-ledger.md"
    audit.parent.mkdir(parents=True)
    audit.write_text(
        "| Orchestrator worktree | `.worktrees/<session>-agents/example-run` |\n"
        "| Orchestrator worktree | `.worktrees/<prefix>-<host>-agents/example-run` |\n"
        "| Leaf worktree | `.worktrees/agents/example-run` |\n",
        encoding="utf-8",
    )

    assert scan_public_docs_for_private_workflow_metadata(tmp_path) == []


def test_given_a_baseline_doc_when_scanned_outside_a_branch_diff_then_it_is_skipped(tmp_path, monkeypatch):
    historical = Path("docs") / "designs" / "historical.md"
    (tmp_path / historical).parent.mkdir(parents=True)
    (tmp_path / historical).write_text("task_id = 1\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_PROVENANCE_DOC_BASELINE", frozenset({historical}))

    assert scan_public_docs_for_private_workflow_metadata(tmp_path) == []


def _git(root: Path, *args: str) -> None:
    # Hooks off and a fixed identity, so the fixture repository never runs the host's hooks.
    config = ["-c", "core.hooksPath=", "-c", "user.name=test", "-c", "user.email=test@example.com"]
    subprocess.run(
        ["git", "-C", str(root), *config, "-c", "commit.gpgsign=false", *args], check=True, capture_output=True
    )


@pytest.fixture
def changed_baseline_doc(tmp_path, monkeypatch):
    """A repository whose ``origin/main`` holds a baseline doc with a historical tracker id."""
    historical = Path("docs") / "designs" / "historical.md"
    (tmp_path / historical).parent.mkdir(parents=True)
    (tmp_path / historical).write_text(
        f"# Historical\n\nFixed in {_PRIVATE_ENVIRONMENT_PREFIX.upper()}-10.\n\nclosing line\n", encoding="utf-8"
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.setattr(sys.modules[__name__], "_PROVENANCE_DOC_BASELINE", frozenset({historical}))
    return tmp_path, historical


def test_given_a_tracker_id_added_to_a_baseline_doc_when_scanned_then_the_added_line_is_flagged(
    changed_baseline_doc,
):
    root, historical = changed_baseline_doc
    added = f"Follow-up in {_PRIVATE_ENVIRONMENT_PREFIX.upper()}-11."
    text = (root / historical).read_text(encoding="utf-8").replace("closing line", f"{added}\nclosing line")
    (root / historical).write_text(text, encoding="utf-8")
    _git(root, "commit", "-q", "-am", "add a line")

    findings = scan_public_docs_for_private_workflow_metadata(root)

    assert findings == [f"docs/designs/historical.md:5: {added}"]


def test_given_a_changed_baseline_doc_when_its_historical_line_is_untouched_then_it_is_not_flagged(
    changed_baseline_doc,
):
    root, historical = changed_baseline_doc
    text = (root / historical).read_text(encoding="utf-8").replace("closing line", "closing line, reworded")
    (root / historical).write_text(text, encoding="utf-8")
    _git(root, "commit", "-q", "-am", "reword")

    assert scan_public_docs_for_private_workflow_metadata(root) == []


class TestTheEnvironmentNameGuardCanActuallyFail:
    """Positive and negative controls for the patterns, in the shapes the names shipped in."""

    @pytest.mark.parametrize(
        "line",
        [
            'CHIEF_PROJECT = "ai-harness"',  # public-hygiene: allow
            '"--firstmate",',  # public-hygiene: allow
            "def _prepare_firstmate_launch(engine):",  # public-hygiene: allow
            'name = "c-r-aih-box-1"',  # public-hygiene: allow
            '_KEY = r"^branch\\..+\\.aih-session-home$"',  # public-hygiene: allow
            'aliases = {"aih": "myproject"}',  # public-hygiene: allow
        ],
    )
    def test_given_a_line_carrying_an_environment_name_when_matched_then_it_is_flagged(self, line):
        assert _line_has_environment_name(line)

    @pytest.mark.parametrize(
        "line",
        [
            "# fixed in AIH-123",  # public-hygiene: allow
            "# a test harness for the launcher",
            'name = "c-r-myproject-box-1"',
        ],
    )
    def test_given_an_ordinary_line_when_matched_then_it_is_not_flagged(self, line):
        assert not _line_has_environment_name(line)

    def test_given_an_offender_in_docs_and_in_src_when_scanned_then_only_src_is_flagged(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "docs").mkdir()
        offender = f"project = {_PRIVATE_ENVIRONMENT_NAMES[0]}\n"
        (tmp_path / "src" / "example.py").write_text(offender)
        (tmp_path / "docs" / "example.md").write_text(offender)

        findings = scan_for_private_environment_names(tmp_path)

        assert len(findings) == 1
        assert findings[0].startswith("src/example.py:1:")


# Identifiers from private issue trackers. A published reference to a tracker no reader can
# open names work they cannot see, so these belong to the same rule as the names above --
# but they are scanned over a NARROWER set of paths, on purpose.
#
# `AI-CLI-` is deliberately absent from the pattern: it is this repository's own prefix, its
# issues ship in `.beads/issues.jsonl`, and the ids already appear in merged commit messages.
# Forbidding them would delete references a reader can actually follow.
# `src`, `scripts` and now `tests`. The omission of `docs` remains a measured scope
# decision rather than an oversight -- the mistake the `scripts` gap above taught.
#
# Measured 2026-09-27 with `grep -r`: after the shipped-source scrub `src` and `scripts`
# both hold 0. `tests` held 22 occurrences across 8 files and is now scrubbed, so it joins
# the scan. `docs` still holds 224 across 33 files and deliberately does NOT: there, an id
# is often the only provenance link a maintainer has, so removing one is a judgement call
# nobody has made rather than a cleanup, and folding a 224-occurrence documentation rewrite
# into any other change would make it unreviewable.
#
# Measure with `grep -r`, NOT `git grep`: git's regex engine does not honour `\b` on every
# host, and the same pattern that matches 42 files under `grep -rlE` matched 0 under
# `git grep -lE`. Re-confirmed on `tests` while scrubbing it -- 8 files under `grep -rlE`,
# 0 under `git grep -lE`. A future reader re-measuring with `git grep` will wrongly conclude
# the remaining work is already done.
_TRACKER_ID_SCANNED_PATHS = ("src", "scripts", "tests")

# One file inside a scanned path is held out, and it is named here rather than exempted
# with an inline marker so the debt is visible in one place instead of hidden at the line.
#
# `tests/conftest.py` carries a single id in a comment. It is excluded only because a
# delegated agent is editing that file right now, and editing another session's file is
# what produced the one merge collision earlier in this batch. An inline
# `public-hygiene: allow` marker would have been the wrong tool: it says "this line is
# legitimate", and this line is not -- it is pending.
#
# Removing the entry is the whole remaining task for `tests`, and the test below asserts
# the set's exact contents, so it cannot silently grow into a general escape hatch.
_TRACKER_ID_PENDING_PATHS = frozenset({Path("tests") / "conftest.py"})


def scan_for_private_tracker_ids(root: Path) -> list[str]:
    """Return ``path:lineno: line`` for every private tracker id under the scanned paths."""
    findings: list[str] = []
    for path, relative_path, text in _scanned_text_files(root, _TRACKER_ID_SCANNED_PATHS):
        del path
        if relative_path in _TRACKER_ID_PENDING_PATHS:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _PRIVATE_TRACKER_ID.search(line) and not _line_is_exempted(relative_path, line):
                findings.append(f"{relative_path.as_posix()}:{lineno}: {line.strip()}")
    return findings


def test_given_the_pending_holdout_set_when_read_then_it_is_exactly_the_known_debt():
    """The held-out set is pinned so it cannot quietly become a general escape hatch.

    A per-file exclusion is the kind of mechanism that starts as one pending file and ends
    as the reason the guard enforces nothing. Pinning the exact contents means adding a
    second file has to be deliberate and reviewed, and means deleting the last entry --
    the actual goal -- shows up here as a required edit rather than being forgotten.
    """
    held_out = sorted(path.as_posix() for path in _TRACKER_ID_PENDING_PATHS)

    assert held_out == ["tests/conftest.py"], (
        f"the pending holdout set changed: {held_out}. Scrub the file and remove it from the set; "
        "do not add new entries to work around the guard."
    )


def test_given_the_shipped_package_and_its_scripts_when_scanned_then_no_private_tracker_ids_remain():
    """Locks in the scrub so a future comment cannot quietly republish a private id."""
    findings = scan_for_private_tracker_ids(_repo_root())
    assert not findings, "private tracker ids in a public package:\n" + "\n".join(findings)


class TestTheTrackerIdGuardCanActuallyFail:
    """Positive controls. A guard nobody has watched go red enforces nothing.

    These drive the pattern rather than the file walk, because the walk is already
    covered by the name guard's own controls and re-walking a temp tree here would
    test the shared helper twice instead of testing this pattern once.
    """

    def test_given_a_line_carrying_a_private_tracker_id_when_matched_then_it_is_flagged(self):
        for identifier in ("AIH-443", "SW-873", "CORE-196", "AIH-emit-safe-subset-jznr"):  # public-hygiene: allow
            assert _PRIVATE_TRACKER_ID.search(f"# fixed in {identifier}"), identifier

    def test_given_this_repositorys_own_id_when_matched_then_it_is_not_flagged(self):
        """The whole point of the exclusion: these references are followable."""
        assert not _PRIVATE_TRACKER_ID.search("# Robust primary path (AI-CLI-94).")
        assert not _PRIVATE_TRACKER_ID.search("# see AI-CLI-2jwm for the scripts gap")

    def test_given_an_ordinary_word_when_matched_then_it_is_not_flagged(self):
        """Guards against the pattern being loose enough to fire on prose."""
        for line in ("# a software-defined switch", "# SWITCH-case handling", "# core-196 lowercase"):
            assert not _PRIVATE_TRACKER_ID.search(line), line


def _ip_is_allowed(address: str) -> bool:
    return address in _ALLOWED_IP_LITERALS or address.startswith(_DOCUMENTATION_IP_PREFIXES)


def scan_for_host_addresses(root: Path) -> list[str]:
    """Return ``path:lineno: line`` for every literal IPv4 address that is not synthetic."""
    findings: list[str] = []
    for path, relative_path, text in _scanned_text_files(root):
        del path
        for lineno, line in enumerate(text.splitlines(), start=1):
            offenders = [address for address in _IPV4.findall(line) if not _ip_is_allowed(address)]
            if offenders and not _line_is_exempted(relative_path, line):
                findings.append(f"{relative_path.as_posix()}:{lineno}: {line.strip()}")
    return findings


def test_given_the_repository_when_scanned_then_no_real_host_address_is_present():
    """An address identifies one machine on someone's network, which is the leak.

    Two reached ``main`` before this guard existed and neither looked like a
    mistake in review: a VPN-range address used as a test fixture, and the same
    address in an archived plan document's example config. Both are the shape this
    catches -- a plausible-looking value nobody thought of as a hostname.

    Scope limit, stated rather than implied: this walks ``_SCANNED_PATHS``, so the
    tracked task-store exports under ``.beads/`` are outside it. Those carry
    historical issue text with two real addresses in it, and scrubbing the export
    would not hold -- it is regenerated from the task store, so the fix belongs in
    the store and is tracked separately rather than being silently skipped here.
    """
    findings = scan_for_host_addresses(_repo_root())
    assert not findings, (
        "literal host addresses in a public package:\n"
        + "\n".join(findings)
        + "\nUse an RFC 5737 documentation address (192.0.2.x) instead."
    )


def test_given_a_routable_host_address_when_scanned_then_it_is_flagged(tmp_path):
    """Positive control, with an address in the range a VPN actually assigns.

    A control using an obviously-public address would pass against a guard that
    allowed the whole CGNAT range -- which is the guard a reserved-range rule
    would have produced, and the range the real leak was in.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    # A CGNAT-range address, which is the range the real leak was in, but not any
    # machine's: carrying the actual leaked value here would defeat the scrub this
    # guard exists to keep in place.
    cgnat_address = "100.127.255.254"  # public-hygiene: allow
    (tmp_path / "src" / "example.py").write_text(f'relay_host = "{cgnat_address}"\n')

    findings = scan_for_host_addresses(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith("src/example.py:1:")


def test_given_documentation_and_placeholder_addresses_when_scanned_then_they_are_not_flagged(tmp_path):
    """Negative control: every value the repository legitimately contains must pass.

    Includes a four-part version string, which the octet bounds are what excludes
    -- a naive dotted-quad pattern matches it and would make this guard noisy
    enough to be switched off.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    lines = [f'addr = "{address}"' for address in sorted(_ALLOWED_IP_LITERALS)]
    lines += [f'doc = "{prefix}42"' for prefix in _DOCUMENTATION_IP_PREFIXES]
    lines.append('version = "2.1.220.300"')
    (tmp_path / "src" / "example.py").write_text("\n".join(lines) + "\n")

    assert scan_for_host_addresses(tmp_path) == []


def test_given_a_private_machine_name_when_scanned_then_it_is_flagged(tmp_path):
    """Per-token control: the left-only boundary must match a real hostname.

    Each token is a PREFIX of the names that actually leaked, so the assertion
    uses the suffixed form. A right boundary passes the bare stem and fails
    the suffixed hostname, which is the entire class inverted.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for index, name in enumerate(_PRIVATE_MACHINE_NAMES):
        (tmp_path / "src" / f"host_{index}.py").write_text(f"# measured on {name}-somehost\n")

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == len(_PRIVATE_MACHINE_NAMES)
    for index, name in enumerate(_PRIVATE_MACHINE_NAMES):
        assert any(finding.startswith(f"src/host_{index}.py:1:") for finding in findings), f"scan did not flag {name!r}"


def test_given_a_nested_offender_when_scanned_then_the_finding_path_is_os_independent(tmp_path):
    """Every assertion above matches a ``/``-separated prefix, so the renderer owes them one.

    This is the contract the other tests depend on rather than state, which is why
    it silently held on POSIX and broke every one of them at once on Windows: a
    plain ``f"{relative_path}"`` renders ``src\\nested\\example.py``, so the
    prefix match fails and the guard reports "scan did not flag" for a name it
    flagged. Asserting no backslash makes the dependency explicit and fails on the
    OS where it is violated.
    """
    nested = tmp_path / "src" / "nested"
    nested.mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (nested / "example.py").write_text(f"# measured on {_PRIVATE_MACHINE_NAMES[0]}-somehost\n")

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith("src/nested/example.py:1:")
    assert "\\" not in findings[0].split(":", 1)[0]


def test_given_the_repository_index_when_listed_then_no_claude_code_install_lock_is_tracked():
    """No path under ``claude/`` may be tracked, whatever it contains.

    ``scan_for_private_names`` above cannot catch this, and the reason is
    structural rather than a missing pattern: it walks an allowlist of eight
    paths (``src``, ``tests``, ``docs``, root metadata, ``.github``), so a
    newly-tracked TOP-LEVEL directory is invisible to it by construction. That is
    how ``claude/locks/2.1.263.lock`` — carrying a contributor's absolute home
    path, which the forbidden-name pattern does match — reached ``main`` with
    every hygiene guard green.

    Claude Code writes that lock to a CWD-relative ``claude/locks/`` rather than
    the user's state directory (an empty-string ``XDG_STATE_HOME`` makes the
    bundle's ``??`` fallback leave the base empty, so the join stays relative), so
    the directory materialises in whatever checkout a session is standing in and
    an automated working-tree sweep committed it. It also broke ``git pull``:
    ``untracked working tree files would be overwritten by checkout``.

    Asserted against the git INDEX, not the filesystem, because the directory is
    expected to exist on disk in any active checkout — being present is normal,
    being *tracked* is the defect. A ``.gitignore`` entry alone would not have
    caught it either: ignore rules do not apply to already-tracked paths.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "claude"],
        capture_output=True,
        text=True,
        check=True,
        cwd=_repo_root(),
    ).stdout
    offenders = [path for path in tracked.split("\0") if path]

    assert not offenders, (
        "Claude Code install-lock artefacts are tracked in a public repository:\n"
        + "\n".join(f"  {path}" for path in offenders)
        + "\nUntrack them with `git rm --cached <path>`; `.gitignore` does not untrack an existing entry."
    )


def _tracked_top_level_dirs(repo_root: Path) -> set[str]:
    """Return the name of every top-level directory with a tracked file under it."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    ).stdout
    return {path.split("/", 1)[0] for path in listing.split("\0") if path and "/" in path}


def test_given_the_repository_index_when_listed_then_no_unexpected_top_level_directory_is_tracked():
    """Generalises the ``claude/`` guard above from one name to the whole class.

    That test was written for one directory name after one incident. The same
    structural hole then admitted a second artefact by the same mechanism: a
    captured process listing at ``ai-cli-utils/remote-ps-cache.json``, written
    there because a set-but-empty ``XDG_STATE_HOME`` left the resolved state
    path relative to the cwd. Both are top-level directories, and
    ``scan_for_private_names`` walks a fixed allowlist of paths, so neither was
    ever in its field of view.

    Enumerating what *may* be tracked inverts that: an artefact directory nobody
    anticipated fails this test on arrival instead of waiting for someone to add
    a third per-name check. The cost is that a genuinely new source directory
    must be added to ``_EXPECTED_TOP_LEVEL_DIRS`` deliberately, which is the
    point -- it is one line, and it forces the question to be asked.

    Asserted against the git INDEX rather than the filesystem: these directories
    are expected to exist on disk in a working checkout. Being present is
    normal; being *tracked* is the defect. Note that a ``.gitignore`` entry
    cannot substitute for this, because ignore rules do not apply to paths that
    are already tracked.

    Scope limit, stated rather than implied: this covers directories only. A
    stray top-level *file* would still pass, because the set of legitimate
    top-level files is large and changes often enough that pinning it would
    produce failures that teach nothing. Both observed incidents were
    directories.
    """
    offenders = sorted(_tracked_top_level_dirs(_repo_root()) - _EXPECTED_TOP_LEVEL_DIRS)

    assert not offenders, (
        "unexpected top-level directories are tracked in a public repository:\n"
        + "\n".join(f"  {name}/" for name in offenders)
        + "\nIf one is a real source directory, add it to _EXPECTED_TOP_LEVEL_DIRS."
        "\nIf it is a generated or machine-specific artefact, untrack it with"
        " `git rm --cached -r <dir>` and add it to .gitignore -- ignoring alone"
        " does not untrack an existing entry."
    )


def test_given_an_unexpected_tracked_top_level_directory_when_checked_then_it_is_flagged(tmp_path):
    """Negative control: the guard must actually be able to fail.

    Builds a real git repository and tracks an artefact directory in it, so the
    assertion exercises ``git ls-files`` rather than a stubbed listing. Without
    this, a guard whose subprocess silently returned nothing would look
    identical to a clean repository.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    artefact = tmp_path / "some-artefact-dir"
    artefact.mkdir()
    (artefact / "cache.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    assert _tracked_top_level_dirs(tmp_path) == {"some-artefact-dir"}


def test_given_only_expected_top_level_directories_when_checked_then_nothing_is_flagged(tmp_path):
    """Positive control: an expected directory must not be reported."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    expected = tmp_path / "src"
    expected.mkdir()
    (expected / "module.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    assert _tracked_top_level_dirs(tmp_path) - _EXPECTED_TOP_LEVEL_DIRS == set()


def test_given_the_machine_local_dolt_server_state_when_checked_then_it_is_ignored():
    """The two guards above cannot see this file, so the ignore rule is the guard.

    ``.beads/.dolt-server-state.json`` records the local task-store server's host,
    pid and port. Those are meaningless in any other clone, so it belongs to the
    machine and not to the repository.

    Neither index-based guard covers it. The install-lock guard names one directory,
    and the top-level-directory guard treats ``.beads`` as expected -- correctly, it
    holds tracked task-store files. So an artefact *inside* ``.beads`` has no guard
    but this one, and it appeared in ``git status`` as untracked where a sweeping
    ``git add -A`` would have taken it.

    Asserted with ``git check-ignore`` against the real repository rather than by
    grepping ``.gitignore`` for the string: an ignore rule that stops matching --
    shadowed by a later negation, or moved into a directory whose scope no longer
    reaches -- fails silently, and a substring match cannot tell the difference. The
    exit status answers the question the rule exists to answer.
    """

    def ignored(path: str) -> bool:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", "--no-index", "--", path],
                cwd=_repo_root(),
                check=False,
            ).returncode
            == 0
        )

    # Control first: a probe that reports everything as ignored would pass the real
    # assertion while measuring nothing.
    assert not ignored(".beads/config.yaml"), (
        "a tracked task-store file reports as ignored, so this probe cannot fail"
        " and proves nothing about the rule below"
    )

    path = ".beads/.dolt-server-state.json"

    assert ignored(path), (
        f"{path} is not ignored: machine-local server state (host, pid, port) can be"
        " committed into a public repository by a sweeping `git add`."
        "\nRestore its entry in .gitignore."
    )


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


def test_given_a_session_name_built_from_the_private_prefix_when_scanned_then_it_is_flagged(tmp_path):
    """Positive control per shape the prefix actually appears in.

    Each string below is a real shape from the leak: a bare session name, an
    engine-prefixed pane name, a worktree directory path, a placeholder written
    with a capital placeholder letter, a named (non-numeric) worktree, and the
    bare argument form whose name the callee completes. A guard that matched only
    ``<prefix>-<digit>`` passes four of these six, and that is not hypothetical --
    it is the pattern the occurrence count was twice measured with, which is why
    it undercounted by 154 occurrences and six files.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    prefix = _PRIVATE_SESSION_PREFIX
    shapes = (
        f"{prefix}-1",
        f"c-{prefix}-1",
        f".worktrees/{prefix}-3",
        f"{prefix}-N",
        f"{prefix}-feature",
        f"c-{prefix}-",
    )
    for index, shape in enumerate(shapes):
        (tmp_path / "src" / f"session_{index}.py").write_text(f'name = "{shape}"\n')

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == len(shapes)
    for index, shape in enumerate(shapes):
        assert any(finding.startswith(f"src/session_{index}.py:1:") for finding in findings), (
            f"scan did not flag {shape!r}"
        )


def test_given_the_private_prefix_as_a_quoted_literal_when_scanned_then_either_case_is_flagged(tmp_path):
    """The prefix also ships as a bare config value, in both cases.

    Both cases are asserted because both leaked: the lowercase form as a session
    prefix in test arguments, the uppercase form as a task prefix quoted in a
    design document. A lowercase-only rule here would leave the second in place.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    lower, upper = _PRIVATE_SESSION_PREFIX, _PRIVATE_SESSION_PREFIX.upper()
    (tmp_path / "src" / "config_example.py").write_text(
        f'session_prefix = "{lower}"\ntask_prefix = "{upper}"\nsingle_quoted = \'{lower}\'\n'
    )

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 3
    assert [finding.split(":")[1] for finding in findings] == ["1", "2", "3"]


def test_given_text_that_merely_contains_the_prefix_letters_when_scanned_then_it_is_not_flagged(tmp_path):
    """Negative control, using the exact strings that forced the two-pattern design.

    Every line here matches a word-bounded case-insensitive rule on the bare
    prefix and none may be flagged:

    * a third-party documentation URL whose host begins with those two letters;
    * a two-letter column abbreviation for an unrelated subsystem in a table;
    * identifiers that merely start with them, where ``_`` is a word character
      and so the boundary alone does not decide;
    * a private dev-tracking id in the uppercase form, which is a **different**
      leak class with its own prefixes and its own issue -- asserting it here
      pins the boundary this guard deliberately draws, so narrowing or widening
      that boundary cannot happen silently.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    upper = _PRIVATE_SESSION_PREFIX.upper()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text(
        "See https://sw.kovidgoyal.net/kitty/keyboard-protocol/ for the protocol.\n"  # public-hygiene: allow
        f"| # | System | {upper} | Built? |\n"
        "sw_client = build()\nhandoff = sw_handoff_dir / name\n"  # public-hygiene: allow
        f"Tracked as {upper}-644 in the other repository's store.\n"
    )

    assert scan_for_private_names(tmp_path) == []


_SESSION_NAME_DATA = Path("src") / "ai_cli" / "data" / "session-names"


def test_given_the_shipped_session_name_data_when_scanned_then_every_file_is_in_view():
    """The generated name list is shipped data, and a first-name source can carry a private name.

    It is covered because it sits under ``src``; asserting each file by path keeps that from
    becoming an assumption, the way ``scripts`` once was.
    """
    scanned = {relative for _path, relative, _text in _scanned_text_files(_repo_root())}

    for filename in ("names.txt", "generation.json", "non-names.txt"):
        assert _SESSION_NAME_DATA / filename in scanned, f"{filename} is outside the hygiene scan"


def test_given_a_personal_name_in_the_session_name_list_when_scanned_then_it_is_flagged(tmp_path):
    data = tmp_path / _SESSION_NAME_DATA
    data.mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (data / "names.txt").write_text(f"ada\n{_PERSONAL_IDENTIFIERS[2]}\nida\n")

    findings = scan_for_private_names(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith(f"{_SESSION_NAME_DATA.as_posix()}/names.txt:2:")


def test_given_a_non_utf8_file_when_scanned_then_it_is_skipped_without_error(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe binary")

    assert scan_for_private_names(tmp_path) == []
