"""The wiki is built from tracked files by the manifest: complete coverage, valid links, stable bytes."""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from ai_cli.main import _cli_group
from scripts import generate_docs as gen
from scripts import wiki_build as wiki

REPO = Path(__file__).resolve().parents[1]
REPOSITORY = "example/myproject"


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


BASE_FILES = {
    "README.md": "# myproject\n\nIntro.\n\n## Install\n\nRun it.\n\n## Use\n\nSee [the guide](docs/guide.md#steps) and [setup](#install).\n",
    "docs/guide.md": "---\ntitle: The Guide\n---\n\n# Ignored heading\n\n## Steps\n\nOne.\n",
    "docs/notes/plain.md": "No heading here.\n",
    "docs/other/plain.md": "# Other plain\n",
}

BASE_MANIFEST = """
[[page]]
name = "Home"
source = "README.md"
preamble = true
sections = ["Install", "Use"]

[[group]]
name = "Docs"
glob = "docs/**/*"
"""


def _build(tmp_path, files=None, manifest=BASE_MANIFEST, out="out"):
    repo = _repo(tmp_path / "repo", {**BASE_FILES, **(files or {}), "docs/wiki/pages.toml": manifest})
    target = tmp_path / out
    written = wiki.build_wiki(repo / wiki.MANIFEST, repo, target, REPOSITORY)
    return repo, target, written


def _fails(tmp_path, manifest, files=None):
    with pytest.raises(wiki.WikiError) as info:
        _build(tmp_path, files=files, manifest=manifest)
    assert not (tmp_path / "out").exists()
    return str(info.value)


# --- T-3.1 manifest validation --------------------------------------------------------


@pytest.mark.parametrize(
    ("manifest", "named"),
    [
        ('[[page]]\nname = "Gone"\nsource = "MISSING.md"\n', "'Gone'"),
        ('[[page]]\nname = "Part"\nsource = "README.md"\nsections = ["Nope"]\n', "'Part'"),
        ('[[page]]\nname = "Odd"\nsource = "README.md"\ncolour = "red"\n', "'Odd'"),
        (
            '[[page]]\nname = "Twice"\nsource = "README.md"\n[[page]]\nname = "Twice"\nsource = "docs/guide.md"\n',
            "'Twice'",
        ),
        ('[[page]]\nname = "Both"\nsource = "README.md"\ngenerated = "cli-reference"\n', "'Both'"),
        ('[[page]]\nname = "Gen"\ngenerated = "nothing-like-this"\n', "'Gen'"),
    ],
    ids=[
        "missing-source",
        "missing-section",
        "unknown-key",
        "duplicate-name",
        "source-and-generated",
        "unknown-generated",
    ],
)
def test_given_a_bad_page_entry_when_the_wiki_is_built_then_it_fails_naming_the_page_and_builds_nothing(
    tmp_path, manifest, named
):
    assert named in _fails(tmp_path, manifest)


@pytest.mark.parametrize(
    ("manifest", "named"),
    [
        (BASE_MANIFEST + '[[group]]\nname = "Empty"\nglob = "nowhere/**/*.md"\n', "'Empty'"),
        (BASE_MANIFEST + '[[group]]\nname = "Nameless"\n', "'Nameless'"),
        (BASE_MANIFEST + '[[group]]\nglob = "src/**"\n', "'src/**'"),
    ],
    ids=["matches-nothing", "no-glob", "no-name"],
)
def test_given_a_bad_group_when_the_wiki_is_built_then_it_fails_naming_the_group_and_builds_nothing(
    tmp_path, manifest, named
):
    assert named in _fails(tmp_path, manifest)


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        ('[[exclude]]\npattern = "*.nothing"\nreason = "none"\n', "'*.nothing'"),
        ('[[exclude]]\npattern = "*.py"\nreason = "  "\n', "'*.py'"),
        ('[[exclude]]\npattern = "*.md"\nreason = "drop markdown"\n', "loose.md"),
    ],
    ids=["matches-no-candidate", "empty-reason", "markdown-match"],
)
def test_given_a_bad_exclusion_when_the_wiki_is_built_then_it_fails_naming_the_exclusion_and_builds_nothing(
    tmp_path, extra, named
):
    manifest = '[[page]]\nname = "Home"\nsource = "README.md"\n[[group]]\nname = "Docs"\nglob = "docs/**/*"\n' + extra
    message = _fails(tmp_path, manifest, files={"tool.py": "print()\n", "loose.md": "# Loose\n"})
    assert named in message


OVERLAP_FILES = {"docs/reference/example.toml": "key = 1\n", "config/tool.toml": "key = 2\n"}
OVERLAP_EXCLUDE = '[[exclude]]\npattern = "*.toml"\nreason = "Configuration outside docs/"\n'


def test_given_a_docs_toml_claimed_and_a_config_toml_unclaimed_when_built_then_only_the_config_file_is_excluded(
    tmp_path,
):
    repo, out, _ = _build(tmp_path, files=OVERLAP_FILES, manifest=BASE_MANIFEST + OVERLAP_EXCLUDE)
    data = tomllib.loads((repo / wiki.MANIFEST).read_text(encoding="utf-8"))

    result = wiki.plan(data, repo, {})

    assert result.excluded == {"config/tool.toml": "Configuration outside docs/"}
    assert (out / "docs-reference-example.toml.md").is_file()
    assert "key = 1" in (out / "docs-reference-example.toml.md").read_text(encoding="utf-8")


def test_given_the_overlap_without_the_group_claim_when_built_then_it_fails_naming_the_exclusion_and_the_docs_file(
    tmp_path,
):
    manifest = '[[page]]\nname = "Home"\nsource = "README.md"\n[[group]]\nname = "Docs"\nglob = "docs/**/*.md"\n'

    message = _fails(tmp_path, manifest + OVERLAP_EXCLUDE, files=OVERLAP_FILES)

    assert "'*.toml'" in message
    assert "docs/reference/example.toml" in message


# --- T-3.2 page build -----------------------------------------------------------------


def test_given_a_manifest_when_built_then_each_page_has_a_file_plus_sidebar_and_footer_in_manifest_order(tmp_path):
    manifest = (
        BASE_MANIFEST.replace('name = "Home"', 'name = "Home"', 1)
        + '[[page]]\nname = "Readme Again"\nsource = "README.md"\nsections = ["Use"]\n'
    )
    manifest = manifest.replace(
        "[[group]]", '[[page]]\nname = "Start"\nsource = "README.md"\nsections = ["Install"]\n\n[[group]]', 1
    )
    _, out, written = _build(tmp_path, manifest=manifest)

    sidebar = (out / "_Sidebar.md").read_text(encoding="utf-8")
    names = re.findall(r"- \[([^\]]+)\]", sidebar)

    assert {p.name for p in written} >= {
        "Home.md",
        "Start.md",
        "Readme-Again.md",
        "Docs.md",
        "_Sidebar.md",
        "_Footer.md",
    }
    assert names == ["Home", "Start", "Readme Again", "Docs"]
    assert "overwritten" in (out / "_Footer.md").read_text(encoding="utf-8")


def test_given_a_page_selecting_sections_when_built_then_it_holds_exactly_those_sections_in_order(tmp_path):
    manifest = '[[page]]\nname = "Home"\nsource = "README.md"\nsections = ["Use", "Install"]\n[[group]]\nname = "Docs"\nglob = "docs/**/*"\n'
    _, out, _ = _build(tmp_path, manifest=manifest)

    text = (out / "Home.md").read_text(encoding="utf-8")

    assert re.findall(r"^## (.+)$", text, flags=re.MULTILINE) == ["Use", "Install"]
    assert "Intro." not in text


def test_given_links_in_a_source_when_built_then_they_point_at_wiki_pages_github_paths_or_raw_images(tmp_path):
    files = {
        "README.md": BASE_FILES["README.md"]
        + "\n## More\n\n![logo](assets/logo.png) [script](tools/run.sh) [docs dir](docs) [guide](docs/guide.md)\n",
        "assets/logo.png": "png",
        "tools/run.sh": "echo\n",
    }
    manifest = BASE_MANIFEST.replace('["Install", "Use"]', '["Install", "Use", "More"]') + (
        '[[exclude]]\npattern = "assets/**"\nreason = "images"\n[[exclude]]\npattern = "*.sh"\nreason = "scripts"\n'
    )
    _, out, _ = _build(tmp_path, files=files, manifest=manifest)

    text = (out / "Home.md").read_text(encoding="utf-8")

    assert "[the guide](docs-guide#steps)" in text
    assert "[setup](#install)" in text
    assert "![logo](https://raw.githubusercontent.com/example/myproject/main/assets/logo.png)" in text
    assert "[script](https://github.com/example/myproject/blob/main/tools/run.sh)" in text
    assert "[docs dir](https://github.com/example/myproject/tree/main/docs)" in text
    assert "[guide](docs-guide)" in text


@pytest.mark.parametrize("fence", ["`", "~"])
def test_given_a_long_outer_fence_when_sections_are_split_then_short_inner_fences_remain_code(fence):
    text = (
        f"{fence * 4}text\ninside\n{fence * 3}\n## Still code\n"
        f"[coded](missing.md)\n{fence * 4}\n## Real\n[guide](docs/guide.md)\n"
    )

    preamble, sections = wiki.split_sections(text)

    assert sections == {"Real": "## Real\n[guide](docs/guide.md)"}
    assert "## Still code" in preamble
    assert re.findall(r"\[[^]]+\]\(([^)]+)\)", sections["Real"]) == ["docs/guide.md"]


def _relative_links(out: Path) -> list[tuple[str, str]]:
    """Every link target outside code that is not a URL or an in-page anchor."""
    links = []
    for page in sorted(out.glob("*.md")):
        for line, fenced in wiki._lines_outside_fences(page.read_text(encoding="utf-8")):
            if fenced:
                continue
            for match in wiki._INLINE_LINK.finditer(wiki._mask_code(line)[0]):
                target = match.group(3) if match.group(3) is not None else match.group(4)
                if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:|^#", target):
                    links.append((page.name, target))
    return links


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("wiki")
    written = gen.build_wiki(REPO, _cli_group, out, REPOSITORY)
    return out, written


def test_given_the_repository_wiki_when_scanned_then_every_relative_link_resolves_to_a_built_page(built):
    out, _ = built
    links = _relative_links(out)
    broken = [f"{page}: {target}" for page, target in links if not (out / f"{target.split('#')[0]}.md").is_file()]

    assert len(links) > 100
    assert broken == []


def test_given_the_repository_when_the_wiki_is_built_then_every_tracked_file_is_published_or_excluded(built):
    out, written = built
    data = tomllib.loads((REPO / wiki.MANIFEST).read_text(encoding="utf-8"))
    generated = {"cli-reference": (gen.REFERENCE, gen.render_reference(_cli_group))}
    result = wiki.plan(data, REPO, generated)
    published = {p.source for p in result.pages} | {m.source for _, members in result.groups for m in members}
    tracked = set(wiki.tracked_files(REPO))
    docs_artifacts = {p for p in tracked if p.startswith("docs/") and not p.endswith(".md")}

    assert published | set(result.excluded) == tracked
    assert published & set(result.excluded) == set()
    assert docs_artifacts <= published
    assert "docs/reference/iterm2-defaults.toml" in docs_artifacts
    assert any(p.startswith("docs/audits/") and p.endswith(".jsonl") for p in docs_artifacts)
    assert {"LICENSE", ".env.example"} <= published
    assert all(not p.endswith(".md") and not p.startswith("docs/") for p in result.excluded)


def test_given_the_repository_wiki_when_built_then_home_and_cli_reference_read_correctly(built):
    out, _ = built
    home = (out / "Home.md").read_text(encoding="utf-8")
    reference = (out / "CLI-Reference.md").read_text(encoding="utf-8")

    assert home.startswith("# ai-cli-utils")
    assert "## What it does" in home
    assert "## ai sync pull" in reference
    assert "| `-p` | `--prefer-remote` |" in reference
    assert "](CLI-Guide)" in reference


def test_given_a_tracked_file_nothing_covers_when_built_then_it_fails_naming_the_file(tmp_path):
    message = _fails(tmp_path, BASE_MANIFEST, files={"NOTICE": "Notice text\n"})

    assert "NOTICE" in message


def test_given_focused_documents_when_built_then_each_is_published(tmp_path):
    files = {
        "LICENSE": "Permission is granted.\n",
        ".github/guide.md": "# Hidden dir guide\n",
        "src/pkg/README.md": "# Package notes\n",
        "docs/data/receipt.jsonl": '{"a": 1}\n',
        "docs/guide.md": BASE_FILES["docs/guide.md"] + "\n[receipt](data/receipt.jsonl)\n",
    }
    manifest = (
        BASE_MANIFEST
        + '[[group]]\nname = "Other Markdown"\nglob = "**/*.md"\n'
        + '[[page]]\nname = "License"\nsource = "LICENSE"\n'
    )
    _, out, _ = _build(tmp_path, files=files, manifest=manifest)

    assert "```text\nPermission is granted.\n```" in (out / "License.md").read_text(encoding="utf-8")
    assert (out / ".github-guide.md").is_file()
    assert (out / "src-pkg-README.md").is_file()
    assert "```json" in (out / "docs-data-receipt.jsonl.md").read_text(encoding="utf-8")
    assert "[receipt](docs-data-receipt.jsonl)" in (out / "docs-guide.md").read_text(encoding="utf-8")


def test_given_a_group_when_built_then_pages_are_named_by_path_and_the_index_uses_the_title_chain(tmp_path):
    _, out, _ = _build(tmp_path)

    index = (out / "Docs.md").read_text(encoding="utf-8")

    assert (out / "docs-notes-plain.md").is_file()
    assert (out / "docs-other-plain.md").is_file()
    assert "- [The Guide](docs-guide)" in index
    assert "- [Other plain](docs-other-plain)" in index
    assert "- [plain](docs-notes-plain)" in index


def test_given_user_pages_and_groups_when_built_then_the_sidebar_shows_user_pages_first_then_groups_in_order(tmp_path):
    manifest = BASE_MANIFEST.replace('glob = "docs/**/*"', 'glob = "docs/notes/*"') + (
        '[[group]]\nname = "Rest"\nglob = "docs/**/*"\n[[page]]\nname = "Guide"\nsource = "docs/guide.md"\n'
    )
    _, out, _ = _build(tmp_path, manifest=manifest)

    sidebar = (out / "_Sidebar.md").read_text(encoding="utf-8")

    assert re.findall(r"- \[([^\]]+)\]", sidebar) == ["Home", "Guide", "Docs", "Rest"]
    assert sidebar.index("User guide") < sidebar.index("Project records")


def test_given_the_same_commit_when_the_wiki_is_built_twice_then_the_trees_are_byte_identical(built, tmp_path):
    out, _ = built
    again = tmp_path / "again"
    gen.build_wiki(REPO, _cli_group, again, REPOSITORY)

    first = {p.name: p.read_bytes() for p in out.glob("*.md")}
    second = {p.name: p.read_bytes() for p in again.glob("*.md")}

    assert first == second
    assert all(b"\r" not in content for content in first.values())


def test_given_an_obsolete_generated_page_when_the_wiki_is_rebuilt_then_only_the_expected_tree_remains(tmp_path):
    _, out, first = _build(tmp_path)
    expected = {path.name: path.read_bytes() for path in first}
    (out / "Obsolete.md").write_text("old\n", encoding="utf-8")

    _, _, rebuilt = _build(tmp_path, out="out")

    assert not (out / "Obsolete.md").exists()
    assert {path.name: path.read_bytes() for path in rebuilt} == expected


def test_given_a_published_source_with_private_metadata_when_built_then_it_fails_with_safe_counts(tmp_path):
    private_reference = "historical " + "CORE" + "-example"

    message = _fails(tmp_path, BASE_MANIFEST, files={"docs/private.md": private_reference + "\n"})

    assert "docs/private.md" in message
    assert "private tracker references: 1" in message
    assert private_reference not in message


def test_given_a_machine_receipt_with_private_metadata_when_built_then_the_published_copy_is_sanitized(tmp_path):
    private_reference = "historical " + "CORE" + "-example"

    _, out, _ = _build(
        tmp_path,
        files={"docs/example.receipt.jsonl": '{"source": "' + private_reference + '"}\n'},
    )

    published = (out / "docs-example.receipt.jsonl.md").read_text(encoding="utf-8")
    assert private_reference not in published
    assert "private-tracker-reference" in published


def test_given_no_repository_when_the_wiki_is_built_then_it_fails_rather_than_guess(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    code = gen.main(["-c", "-w", str(tmp_path / "out")])

    assert code == 1
    assert "OWNER/REPO" in capsys.readouterr().err


# --- T-3.3 publishing workflow ----------------------------------------------------------


@pytest.fixture(scope="module")
def workflow():
    text = (REPO / ".github/workflows/wiki.yml").read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_given_the_wiki_workflow_when_read_then_it_runs_on_main_pushes_and_manual_retry_only(workflow):
    _, data = workflow
    triggers = data.get("on", data.get(True))

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]


def test_given_the_jobs_when_read_then_both_require_main_and_the_builder_runs_only_after_checking_out_main(workflow):
    _, data = workflow
    build, publish = data["jobs"]["build"], data["jobs"]["publish"]
    first_run = next(i for i, step in enumerate(build["steps"]) if "run" in step)
    checkout = build["steps"][0]

    assert build["if"] == publish["if"] == "github.ref == 'refs/heads/main'"
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "refs/heads/main"
    assert first_run > 0
    assert any("generate_docs.py" in step.get("run", "") for step in build["steps"][first_run:])


def test_given_the_publish_job_when_read_then_only_it_can_write_and_it_runs_first_party_actions_only(workflow):
    text, data = workflow
    build, publish = data["jobs"]["build"], data["jobs"]["publish"]
    actions = [step["uses"] for step in publish["steps"] if "uses" in step]

    assert data["permissions"] == build["permissions"] == {"contents": "read"}
    assert publish["permissions"] == {"contents": "write"}
    assert publish["needs"] == "build"
    assert actions and all(action.startswith("actions/") for action in actions)
    assert all(re.search(r"@[0-9a-f]{40}\b", line) for line in text.splitlines() if "uses:" in line)
    assert "${{ github.repository }}" in text


def test_given_the_publish_job_when_read_then_it_names_the_first_page_step_and_stops_on_a_token_failure(workflow):
    text, _ = workflow

    assert "create the first wiki page once in the GitHub web interface" in text
    assert "maintainer" in text
    assert "git diff --cached --quiet" in text
    assert "secrets." not in text.replace("secrets.GITHUB_TOKEN", "")
