"""Build the GitHub wiki tree from the repository's own tracked files.

``docs/wiki/pages.toml`` lists the user pages (a whole file, named ``##`` sections of a
file, or a generated page) and the groups that publish every other document. Every
tracked file must be claimed by a page or a group, or matched by an exclusion for a class
of non-documents; the build fails naming any file that is neither. Links are rewritten so
that nothing in the wiki points at a path the wiki does not have.

Used by ``scripts/generate_docs.py -w DIR``; no network, no configuration.
"""

import posixpath
import re
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

try:
    from .publication_hygiene import publication_violations, sanitize_public_provenance
except ImportError:  # run as `python scripts/wiki_build.py`
    from publication_hygiene import publication_violations, sanitize_public_provenance

MANIFEST = "docs/wiki/pages.toml"
RESERVED = {"_Sidebar", "_Footer"}
_PAGE_KEYS = {"name", "source", "sections", "preamble", "generated"}
_GROUP_KEYS = {"name", "glob"}
_EXCLUDE_KEYS = {"pattern", "reason"}
# An inline link or image: [text](target "title"), the target bare or in angle brackets.
_INLINE_LINK = re.compile(r"(!?)\[([^\]]*)\]\((?:<([^>]*)>|([^)\s]*))((?:\s+\"[^\"]*\")?)\)")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_LANGUAGES = {
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".jsonl": "json",
    ".json5": "json5",
    ".sh": "bash",
    ".py": "python",
}


class WikiError(Exception):
    """A manifest or coverage problem; nothing is built."""


@dataclass
class Page:
    name: str
    title: str
    source: str
    body: str = ""
    sections: list[str] = field(default_factory=list)
    preamble: bool = False
    generated: str = ""
    group: str = ""

    @property
    def slug(self) -> str:
        return slug(self.name)


@dataclass
class Plan:
    pages: list[Page]
    groups: list[tuple[str, list[Page]]]
    excluded: dict[str, str]


def slug(name: str) -> str:
    """The wiki file stem and link target for a page name."""
    return name.replace(" ", "-")


def matches(pattern: str, path: str) -> bool:
    """The manifest matcher: case-sensitive; a slashless pattern matches the basename at any depth.

    In a pattern with a slash, ``*`` stays within one path component and ``**`` matches zero
    or more complete components.
    """
    if "/" not in pattern:
        return fnmatchcase(posixpath.basename(path), pattern)
    return _match_parts(pattern.split("/"), path.split("/"))


def _match_parts(pattern: list[str], parts: list[str]) -> bool:
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return any(_match_parts(pattern[1:], parts[i:]) for i in range(len(parts) + 1))
    return bool(parts) and fnmatchcase(parts[0], pattern[0]) and _match_parts(pattern[1:], parts[1:])


def tracked_files(repo: Path) -> list[str]:
    """Every tracked file, repository-relative with forward slashes."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True).stdout
    return sorted(Path(p).as_posix() for p in out.decode("utf-8").split("\0") if p)


def anchor(heading: str) -> str:
    """The anchor GitHub gives a heading."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip())
    text = re.sub(r"[^\w\- ]", "", text.lower().replace("`", ""))
    return text.replace(" ", "-")


def _lines_outside_fences(text: str) -> list[tuple[str, bool]]:
    fence: tuple[str, int] | None = None
    out = []
    for line in text.split("\n"):
        match = _FENCE.match(line)
        if match and not fence:
            fence = (match.group(1)[0], len(match.group(1)))
            out.append((line, True))
        elif fence and re.fullmatch(rf"\s*{re.escape(fence[0])}{{{fence[1]},}}\s*", line):
            fence = None
            out.append((line, True))
        else:
            out.append((line, bool(fence)))
    return out


def split_sections(text: str) -> tuple[str, dict[str, str]]:
    """``(preamble, {heading: section})`` for the ``##`` sections of a Markdown file."""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] = preamble
    for line, fenced in _lines_outside_fences(text):
        if not fenced and line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        current.append(line)
    return "\n".join(preamble).strip("\n"), {k: "\n".join(v).strip("\n") for k, v in sections.items()}


def headings(text: str) -> list[str]:
    return [
        line.lstrip("#").strip()
        for line, fenced in _lines_outside_fences(text)
        if not fenced and re.match(r"#{1,6} ", line)
    ]


def title_of(path: str, text: str | None) -> str:
    """Frontmatter ``title``, else the first ``#`` heading, else the normalized filename."""
    if text is not None and path.endswith(".md"):
        if text.startswith("---\n"):
            front = text[4:].split("\n---", 1)[0]
            for line in front.splitlines():
                if line.startswith("title:"):
                    value = line[6:].strip().strip("\"'")
                    if value:
                        return value
        for line, fenced in _lines_outside_fences(text):
            if not fenced and line.startswith("# ") and line[2:].strip():
                return line[2:].strip()
    stem = posixpath.basename(path)
    stem = stem[: -len(".md")] if stem.endswith(".md") else stem
    return re.sub(r"[-_]+", " ", stem).strip() or stem


def _read(repo: Path, rel: str) -> str | None:
    try:
        return (repo / rel).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def _check_keys(entry: dict, allowed: set[str], kind: str) -> None:
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise WikiError(
            f"{kind} {entry.get('name') or entry.get('pattern') or '?'!r}: unknown key {', '.join(unknown)}"
        )


def plan(manifest: dict, repo: Path, generated: dict[str, tuple[str, str]]) -> Plan:
    """Validate the manifest against the tracked files and assign every file to one place.

    ``generated`` maps a ``generated`` value to ``(the tracked path it stands for, its content)``.
    """
    unknown = sorted(set(manifest) - {"page", "group", "exclude"})
    if unknown:
        raise WikiError(f"manifest: unknown table {', '.join(unknown)}")
    tracked = tracked_files(repo)
    tracked_set = set(tracked)
    names: set[str] = set(RESERVED)
    claimed: set[str] = set()
    pages: list[Page] = []
    selected_sections: dict[str, set[str]] = {}

    def take(name: str, kind: str) -> None:
        if not isinstance(name, str) or not name:
            raise WikiError(f"{kind}: missing name")
        if name in names or slug(name) in {slug(n) for n in names}:
            raise WikiError(f"{kind} {name!r}: duplicate page name")
        names.add(name)

    for entry in manifest.get("page", []):
        _check_keys(entry, _PAGE_KEYS, "page")
        name = entry.get("name", "")
        take(name, "page")
        has_source, has_generated = "source" in entry, "generated" in entry
        if has_source == has_generated:
            raise WikiError(f"page {name!r}: needs exactly one of source or generated")
        if ("sections" in entry or "preamble" in entry) and not has_source:
            raise WikiError(f"page {name!r}: sections and preamble need a source")
        if has_generated:
            if entry["generated"] not in generated:
                raise WikiError(f"page {name!r}: unknown generated page {entry['generated']!r}")
            source, body = generated[entry["generated"]]
            pages.append(Page(name, name, source, body=body, generated=entry["generated"]))
            claimed.add(source)
            continue
        source = entry["source"]
        if source not in tracked_set:
            raise WikiError(f"page {name!r}: source {source} is not a tracked file")
        text = _read(repo, source)
        if text is None:
            raise WikiError(f"page {name!r}: source {source} is not UTF-8 text")
        wanted = list(entry.get("sections", []))
        if wanted:
            _, sections = split_sections(text)
            missing = [s for s in wanted if s not in sections]
            if missing:
                raise WikiError(f"page {name!r}: {source} has no ## section {', '.join(missing)}")
            selected_sections.setdefault(source, set()).update(wanted)
        pages.append(Page(name, name, source, sections=wanted, preamble=bool(entry.get("preamble"))))
        claimed.add(source)

    for source, chosen in selected_sections.items():
        text = _read(repo, source) or ""
        _, sections = split_sections(text)
        if any(not p.sections for p in pages if p.source == source):
            continue
        left = [s for s in sections if s not in chosen]
        if left:
            raise WikiError(f"{source}: no page selects the ## section {', '.join(left)}")

    groups: list[tuple[str, list[Page]]] = []
    for entry in manifest.get("group", []):
        _check_keys(entry, _GROUP_KEYS, "group")
        name, pattern = entry.get("name", ""), entry.get("glob", "")
        if not name or not pattern:
            raise WikiError(f"group {name or pattern or '?'!r}: needs both name and glob")
        take(name, "group")
        hits = [p for p in tracked if matches(pattern, p)]
        if not hits:
            raise WikiError(f"group {name!r}: glob {pattern!r} matches no tracked file")
        members = []
        for rel in hits:
            if rel in claimed:
                continue
            claimed.add(rel)
            member = rel[: -len(".md")] if rel.endswith(".md") else rel
            page_name = member.replace("/", "-")
            take(page_name, f"group {name!r} page")
            members.append(Page(page_name, title_of(rel, _read(repo, rel)), rel, group=name))
        groups.append((name, members))

    candidates = [p for p in tracked if p not in claimed]
    excluded: dict[str, str] = {}
    for entry in manifest.get("exclude", []):
        _check_keys(entry, _EXCLUDE_KEYS, "exclusion")
        pattern, reason = entry.get("pattern", ""), str(entry.get("reason", "")).strip()
        if not reason:
            raise WikiError(f"exclusion {pattern!r}: empty reason")
        hits = [p for p in candidates if matches(pattern, p)]
        if not hits:
            raise WikiError(f"exclusion {pattern!r}: matches no unclaimed file")
        documents = [p for p in hits if p.endswith(".md") or p.startswith("docs/")]
        if documents:
            raise WikiError(f"exclusion {pattern!r}: would exclude documents {', '.join(documents)}")
        for rel in hits:
            excluded.setdefault(rel, reason)

    uncovered = [p for p in candidates if p not in excluded]
    if uncovered:
        raise WikiError(f"tracked files neither published nor excluded: {', '.join(uncovered)}")
    return Plan(pages, groups, excluded)


def _mask_code(line: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def keep(match: re.Match) -> str:
        spans.append(match.group(0))
        return f"\0{len(spans) - 1}\0"

    return re.sub(r"`[^`]*`", keep, line), spans


def _unmask(text: str, spans: list[str]) -> str:
    return re.sub(r"\0(\d+)\0", lambda m: spans[int(m.group(1))], text)


class _Links:
    """Rewrites repository-relative links for the wiki."""

    def __init__(self, plan: Plan, repo: Path, repository: str):
        self.blob = f"https://github.com/{repository}/blob/main/"
        self.tree = f"https://github.com/{repository}/tree/main/"
        self.raw = f"https://raw.githubusercontent.com/{repository}/main/"
        self.whole: dict[str, str] = {}
        self.by_anchor: dict[str, dict[str, str]] = {}
        self.first: dict[str, str] = {}
        self.dirs = {"/".join(p.split("/")[:i]) for p in tracked_files(repo) for i in range(1, p.count("/") + 1)}
        for page in plan.pages + [m for _, members in plan.groups for m in members]:
            if page.sections or page.preamble:
                self.first.setdefault(page.source, page.slug)
                text = _read(repo, page.source) or ""
                preamble, sections = split_sections(text)
                parts = ([preamble] if page.preamble else []) + [sections[s] for s in page.sections]
                for part in parts:
                    for heading in headings(part):
                        self.by_anchor.setdefault(page.source, {}).setdefault(anchor(heading), page.slug)
            else:
                self.whole[page.source] = page.slug

    def target(self, source: str, link: str, image: bool) -> str | None:
        """Where ``link`` in ``source`` points in the wiki; ``None`` when it leaves the repository."""
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:|^//|^#", link) or not link:
            return link
        path, _, fragment = link.partition("#")
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
        if rel == ".." or rel.startswith("../"):
            return None
        suffix = f"#{fragment}" if fragment else ""
        if image:
            return self.raw + rel
        if rel in self.whole:
            return self.whole[rel] + suffix
        if rel in self.first:
            page = self.by_anchor.get(rel, {}).get(fragment, self.first[rel]) if fragment else self.first[rel]
            return page + suffix
        if rel in self.dirs or rel == ".":
            return self.tree + ("" if rel == "." else rel)
        return self.blob + rel + suffix

    def _inline(self, source: str, match: re.Match) -> str:
        bang, text, angled, bare, title = match.groups()
        target = self.target(source, angled if angled is not None else bare, bool(bang))
        if target is None:
            return text
        return f"{bang}[{text}]({f'<{target}>' if angled is not None else target}{title})"

    def _reference(self, source: str, match: re.Match) -> str:
        target = self.target(source, match.group(2), False)
        return match.group(0) if target is None else match.group(1) + target

    def _attribute(self, source: str, match: re.Match) -> str:
        target = self.target(source, match.group(2), match.group(1) == "src")
        return match.group(0) if target is None else f'{match.group(1)}="{target}"'

    def rewrite(self, source: str, text: str) -> str:
        out = []
        for line, fenced in _lines_outside_fences(text):
            if fenced:
                out.append(line)
                continue
            # Code spans are masked, not split on, so a link whose text is code still matches.
            masked, spans = _mask_code(line)
            masked = re.sub(_INLINE_LINK, lambda m: self._inline(source, m), masked)
            masked = re.sub(r"^(\s*\[[^\]]+\]:\s*)(\S+)", lambda m: self._reference(source, m), masked)
            masked = re.sub(r"\b(src|href)=\"([^\"]+)\"", lambda m: self._attribute(source, m), masked)
            out.append(_unmask(masked, spans))
        return "\n".join(out)


def _label(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _as_text_page(rel: str, text: str) -> str:
    language = _LANGUAGES.get(posixpath.splitext(rel)[1], "text")
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"# {rel}\n\n{fence}{language}\n{text.rstrip(chr(10))}\n{fence}"


def _publication_text(source: str, text: str) -> str:
    """Render machine receipts and audit carriers as public-safe provenance."""
    if source.endswith(".receipt.jsonl") or (source.startswith("docs/audits/") and source.endswith(".md")):
        return sanitize_public_provenance(text)
    return text


def _body(page: Page, repo: Path, links: _Links) -> str:
    if page.generated:
        text = page.body
    else:
        raw = _read(repo, page.source)
        if raw is None:
            return f"# {page.source}\n\n[Download {page.source}]({links.blob}{page.source})"
        raw = _publication_text(page.source, raw)
        if not page.source.endswith(".md"):
            return _as_text_page(page.source, raw)
        text = raw
        if page.sections or page.preamble:
            preamble, sections = split_sections(raw)
            text = "\n\n".join(([preamble] if page.preamble else []) + [sections[s] for s in page.sections])
    return links.rewrite(page.source, text.strip("\n"))


def build_wiki(
    manifest: Path,
    repo_root: Path,
    out_dir: Path,
    repository: str,
    generated: dict[str, Callable[[], tuple[str, str]]] | None = None,
) -> list[Path]:
    """Write the wiki tree for ``repository`` (``owner/repo``) into ``out_dir``; return the files written."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository or ""):
        raise WikiError(f"repository must be OWNER/REPO, got {repository!r}")
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise WikiError(f"{manifest}: {exc}") from exc
    made = {key: make() for key, make in (generated or {}).items()}
    result = plan(data, repo_root, made)
    violations = []
    published = {page.source for page in result.pages}
    published.update(page.source for _, members in result.groups for page in members)
    for source in sorted(published):
        text = _read(repo_root, source)
        if text is None:
            continue
        text = _publication_text(source, text)
        counts = publication_violations(text)
        if counts:
            details = []
            if counts.private_tracker_references:
                details.append(f"private tracker references: {counts.private_tracker_references}")
            if counts.private_environment_references:
                details.append(f"private environment references: {counts.private_environment_references}")
            violations.append(f"{source} ({', '.join(details)})")
    if violations:
        raise WikiError("published sources fail public-content policy: " + "; ".join(violations))
    links = _Links(result, repo_root, repository)
    files: dict[str, str] = {}
    footer = f"Source: [`{{source}}`]({links.blob}{{source}}). Generated from the repository; edits made here are overwritten."
    for page in result.pages + [m for _, members in result.groups for m in members]:
        files[page.slug] = f"{_body(page, repo_root, links)}\n\n---\n\n{footer.format(source=page.source)}\n"
    for name, members in result.groups:
        rows = [f"- [{_label(m.title)}]({m.slug}) (`{m.source}`)" for m in members]
        files[slug(name)] = "\n".join([f"# {name}", "", *rows]) + "\n"
    sidebar = ["**User guide**", "", *(f"- [{p.name}]({p.slug})" for p in result.pages), ""]
    sidebar += ["**Project records**", "", *(f"- [{name}]({slug(name)})" for name, _ in result.groups)]
    files["_Sidebar"] = "\n".join(sidebar) + "\n"
    files["_Footer"] = (
        "This wiki is generated from the repository by `scripts/generate_docs.py` on every push to `main`. "
        "Each page names its source file; edit that file, not the wiki, since wiki edits are overwritten.\n"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = {f"{stem}.md" for stem in files}
    for stale in sorted(out_dir.glob("*.md")):
        if stale.name not in expected:
            stale.unlink()
    written = []
    for stem, content in files.items():
        path = out_dir / f"{stem}.md"
        path.write_bytes(content.encode("utf-8"))
        written.append(path)
    return written
