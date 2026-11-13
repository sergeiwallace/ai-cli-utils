"""The CLI reference and README index are generated from the command tree and cannot drift.

Also checks that every ``ai ...`` example in the hand-written docs names a real command
and real options, and that the narrative guide defers to the generated reference.
"""

import os
import re
import shlex
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from ai_cli.cli_help import command_summary
from ai_cli.main import SESSION_CONTEXT, _cli_group
from scripts import generate_docs as gen

REPO = Path(__file__).resolve().parents[1]


def _flatten(text):
    return " ".join(re.sub(r"(?<=\w-)\n\s+", "", text).split())


def _sections(reference):
    """``{command path: section text}`` for every ``## ai ...`` section."""
    parts = re.split(r"^## (ai(?: [\w-]+)*)\n", reference, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


@pytest.fixture(scope="module")
def reference():
    return gen.render_reference(_cli_group)


@pytest.fixture(scope="module")
def entries():
    return list(gen.walk(_cli_group))


# --- T-2.1 reference renderer -------------------------------------------------------


def test_given_the_tree_when_the_reference_is_generated_then_it_has_one_section_per_visible_command_in_tree_order(
    reference, entries
):
    assert list(_sections(reference)) == [entry.path for entry in entries]
    assert len(entries) > 90


def test_given_each_command_when_rendered_then_its_section_carries_the_usage_line_click_prints(reference, entries):
    sections = _sections(reference)
    wrong = []
    for entry in entries:
        args = entry.path.split()[1:]
        printed = CliRunner().invoke(_cli_group, [*args, "-h"], terminal_width=80).output
        usage = _flatten(printed.split("\n\n", 1)[0]).removeprefix("Usage: ")
        if f"```text\n{' '.join(usage.split())}\n```" not in sections[entry.path]:
            wrong.append(entry.path)
    assert wrong == []


def test_given_each_command_when_rendered_then_its_options_table_holds_the_help_records_click_prints(
    reference, entries
):
    sections = _sections(reference)
    missing = []
    for entry in entries:
        args = entry.path.split()[1:]
        printed = _flatten(CliRunner().invoke(_cli_group, [*args, "-h"], terminal_width=80).output)
        for param in entry.command.get_params(entry.ctx):
            record = param.get_help_record(entry.ctx) if isinstance(param, click.Option) else None
            if record is None:
                continue
            if gen._cell(record[1]) not in sections[entry.path] or _flatten(record[1]) not in printed:
                missing.append(f"{entry.path} {record[0]}")
    assert missing == []


def test_given_each_command_when_rendered_then_its_full_description_is_in_its_section(reference, entries):
    from ai_cli.cli_help import command_description

    sections = _sections(reference)
    missing = [
        entry.path
        for entry in entries
        if _flatten(gen._inline(command_description(entry.command, entry.ctx))) not in _flatten(sections[entry.path])
    ]
    assert missing == []


def test_given_an_option_with_both_forms_when_rendered_then_each_form_has_its_own_column(reference):
    section = _sections(reference)["ai sync push"]

    assert "| `-f` | `--force` |  | " in section
    assert "| `-n` | `--dry-run` |  | " in section


def _fixture_group():
    group = click.Group("tool")

    @group.command("show", help="Show items.\n\n\b\nfirst line\nsecond line\n\n\fInternal note for maintainers.")
    @click.option("-a", "--all", "show_all", is_flag=True, help="Show every item")
    @click.option("-s", "--secret", hidden=True, help="Hidden option text")
    def show(show_all, secret):
        pass

    @group.command("internal", hidden=True, help="A hidden command")
    def internal():
        pass

    return group


def test_given_help_with_form_feed_and_no_rewrap_markers_when_rendered_then_it_matches_what_h_shows():
    text = gen.render_reference(_fixture_group())

    assert "```text\nfirst line\nsecond line\n```" in text
    assert "Internal note" not in text
    assert "Show items." in text


def test_given_a_hidden_command_or_option_when_rendered_then_the_reference_does_not_mention_it():
    text = gen.render_reference(_fixture_group())

    assert "internal" not in text
    assert "--secret" not in text
    assert "Hidden option text" not in text
    assert "`--all`" in text


def test_given_the_same_tree_when_rendered_twice_or_at_another_terminal_width_then_the_bytes_are_identical(reference):
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((200, 50))):
        wide = gen.render_reference(_cli_group)

    assert gen.render_reference(_cli_group) == reference
    assert wide == reference
    assert "\r" not in reference


def test_given_the_reference_when_generated_then_its_index_lists_every_command_with_its_summary_and_link(
    reference, entries
):
    index = reference.split("\n## Commands\n", 1)[1].split("\n## ", 1)[0]
    missing = [
        entry.path
        for entry in entries[1:]
        if f"| [`{entry.path}`](#{gen.anchor(entry.path)}) | {gen._cell(command_summary(entry.command))} |" not in index
    ]
    assert missing == []


# --- T-2.2 README region -------------------------------------------------------------


def test_given_a_readme_with_markers_when_the_region_is_replaced_then_every_other_byte_is_unchanged():
    text = f"head\r\nkeep\n{gen.BEGIN}\nold index\n{gen.END}\ntail  \n"

    result = gen.replace_region(text, "new index\n", "README.md")

    assert result == f"head\r\nkeep\n{gen.BEGIN}\nnew index\n{gen.END}\ntail  \n"


@pytest.mark.parametrize(
    "readme",
    [
        "no markers\n",
        f"{gen.BEGIN}\nonly begin\n",
        f"{gen.BEGIN}\n{gen.END}\n{gen.BEGIN}\n{gen.END}\n",
        f"{gen.END}\n{gen.BEGIN}\n",
    ],
    ids=["none", "begin-only", "twice", "reversed"],
)
def test_given_missing_or_repeated_markers_when_generating_then_it_exits_non_zero_naming_the_file_and_writes_nothing(
    readme, tmp_path, monkeypatch, capsys
):
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    monkeypatch.setattr(gen, "REPO", tmp_path)

    code = gen.main([])

    assert code == 1
    assert "README.md" in capsys.readouterr().err
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == readme
    assert not (tmp_path / gen.REFERENCE).exists()


# --- T-2.3 generator and check mode ----------------------------------------------------


def _regions():
    return f"{gen.START_BEGIN}\n{gen.START_END}\n\n{gen.BEGIN}\n{gen.END}\n"


@pytest.fixture
def generated_repo(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text(f"# Project\n\n{_regions()}\nMore.\n", encoding="utf-8")
    (tmp_path / gen.GUIDE).parent.mkdir(parents=True)
    (tmp_path / gen.GUIDE).write_text(f"# Guide\n\n{_regions()}", encoding="utf-8")
    monkeypatch.setattr(gen, "REPO", tmp_path)
    assert gen.main([]) == 0
    return tmp_path


def test_given_an_up_to_date_tree_when_checked_then_it_writes_nothing_and_exits_0(generated_repo, capsys):
    before = {p: p.read_bytes() for p in generated_repo.rglob("*") if p.is_file()}

    code = gen.main(["-c"])

    assert code == 0
    assert {p: p.read_bytes() for p in generated_repo.rglob("*") if p.is_file()} == before
    assert capsys.readouterr().out == ""


def test_given_changed_help_and_no_regeneration_when_checked_then_it_exits_1_with_a_diff_and_the_fix(
    generated_repo, capsys, monkeypatch
):
    capsys.readouterr()
    monkeypatch.setattr(_cli_group.commands["attach"], "help", "Attach to a session, changed wording")

    code = gen.main(["--check"])

    out, err = capsys.readouterr()
    assert code == 1
    assert "--- a/docs/reference/cli.md" in out
    assert "+Attach to a session, changed wording" in out
    assert "--- a/README.md" in out
    assert f"--- a/{gen.GUIDE}" in out
    assert gen.REGENERATE in err


def test_given_the_committed_reference_and_readme_when_compared_with_the_tree_then_neither_is_stale():
    diff = gen.stale(REPO, _cli_group)

    assert diff == "", f"Generated docs are stale. Run: {gen.REGENERATE}\n{diff}"


def _region(text, begin, end):
    return text.split(begin, 1)[1].split(end, 1)[0]


@pytest.mark.parametrize("rel", [gen.README, gen.GUIDE])
def test_given_the_readme_and_the_guide_when_compared_with_ai_h_then_they_list_the_same_commands_and_start_rows(rel):
    from test_cli_help import tree_paths

    from ai_cli.cli_help import START_RESUME_ATTACH

    text = (REPO / rel).read_text(encoding="utf-8")
    help_text = CliRunner().invoke(_cli_group, ["-h"], terminal_width=80).output
    index = re.findall(r"^\| \[`ai ([^`]+)`\]", _region(text, gen.BEGIN, gen.END), flags=re.MULTILINE)
    start = re.findall(r"^\| `([^`]+)` \|", _region(text, gen.START_BEGIN, gen.START_END), flags=re.MULTILINE)

    assert index == tree_paths(help_text)
    assert start == [command for command, _ in START_RESUME_ATTACH]
    assert all(command in help_text for command in start)


def test_given_the_generator_when_h_is_passed_then_every_option_shows_both_forms(capsys):
    with pytest.raises(SystemExit) as exit_info:
        gen.main(["-h"])

    out = capsys.readouterr().out
    option_lines = [line.strip() for line in out.splitlines() if line.strip().startswith("-")]
    assert exit_info.value.code == 0
    assert option_lines
    assert [line for line in option_lines if not re.match(r"-\w[ ,].*--\w", line)] == []


# --- T-2.4 examples in hand-written docs ------------------------------------------------

EXAMPLE_DOCS = ["README.md", "docs/tools/*.md", "docs/guides/*.md"]
EXEMPT = "docs-example: skip"
PASS_THROUGH = {name for name, cmd in _cli_group.commands.items() if cmd.context_settings == SESSION_CONTEXT}
_FENCE = re.compile(r"^\s*(```|~~~)")


def _example_lines(path):
    in_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        stripped = line.strip().removeprefix("$ ")
        if in_fence and stripped.startswith("ai "):
            yield number, stripped


def _tokens(line):
    try:
        return shlex.split(line, comments=True)
    except ValueError:
        return line.split("#", 1)[0].split()


def _clean(token):
    return token.strip("[]").rstrip(".,").removesuffix("...")


def _option_names(command, ctx):
    names = {}
    for param in command.get_params(ctx):
        if isinstance(param, click.Option):
            for name in (*param.opts, *param.secondary_opts):
                names[name] = param
    return names


def classify(line):
    """``(findings, pass_through)`` for one example: unknown tokens, and provider pass-through options."""
    tokens = _tokens(line)[1:]
    # Contexts carry each command's settings: Click caches a command's help option from the
    # first context it sees, so a bare context would leave `-h` unrecognised for later tests.
    command, ctx, path = _cli_group, gen._context(_cli_group, "ai", None), ["ai"]
    findings, passed = [], []
    takes_value = False
    commands_open = True
    for raw in tokens:
        token = _clean(raw)
        if not token or token.startswith(("<", "$")) or token in ("|", "&&", ";"):
            takes_value = False
            continue
        if takes_value:
            takes_value = False
            continue
        if token == "--":
            break
        if token.startswith("-"):
            for alternative in token.replace("|", "/").split("/"):
                name = alternative.split("=", 1)[0]
                if not name.startswith("-"):
                    continue
                param = _option_names(command, ctx).get(name)
                if param is not None:
                    takes_value = not (param.is_flag or param.count) and "=" not in alternative
                elif path[-1] in PASS_THROUGH and len(path) == 2:
                    passed.append(name)
                else:
                    findings.append(name)
            continue
        if commands_open and isinstance(command, click.Group):
            names = token.split("/")
            found = [command.get_command(ctx, name) for name in names]
            if all(found):
                command = found[0]
            elif hasattr(command, "resolve_command") and type(command).__name__ == "_LayoutGroup":
                command = command.commands["_apply"]
            else:
                findings.append(token)
                break
            path.append(names[0])
            ctx = gen._context(command, names[0], ctx)
            continue
        commands_open = False
    return findings, passed


def _doc_paths():
    return sorted({p for pattern in EXAMPLE_DOCS for p in REPO.glob(pattern)})


def test_given_the_hand_written_docs_when_their_ai_examples_are_resolved_then_every_command_and_option_exists(
    record_property, capsys
):
    findings, exempt, checked, passed = [], 0, 0, 0
    for path in _doc_paths():
        for number, line in _example_lines(path):
            if EXEMPT in line:
                exempt += 1
                continue
            checked += 1
            unknown, through = classify(line)
            passed += len(through)
            findings += [f"{path.relative_to(REPO)}:{number}: unknown {token!r} in: {line}" for token in unknown]
    record_property("exempt_example_lines", exempt)
    with capsys.disabled():
        print(f"\n[docs examples] {checked} checked, {exempt} exempt, {passed} provider pass-through options")

    assert checked > 50
    assert findings == []


@pytest.mark.parametrize(
    ("line", "unknown"),
    [
        ("ai nosuch-command", ["nosuch-command"]),
        ("ai sync push --nope", ["--nope"]),
        ("ai sync bogus -n", ["bogus"]),
        ("ai ls --model opus", ["--model"]),
    ],
)
def test_given_an_example_naming_a_missing_command_or_option_when_classified_then_the_token_is_reported(line, unknown):
    assert classify(line)[0] == unknown


def test_given_an_example_with_a_missing_option_when_the_docs_check_runs_then_it_names_file_line_and_token(tmp_path):
    doc = tmp_path / "guide.md"
    doc.write_text("Intro\n\n```bash\nai ls -a\nai sync push --nope\n```\n", encoding="utf-8")

    findings = [(number, token) for number, line in _example_lines(doc) for token in classify(line)[0]]

    assert findings == [(5, "--nope")]


def test_given_a_provider_option_on_a_launcher_when_classified_then_it_is_pass_through_not_unknown():
    unknown, passed = classify("ai c myproject --model opus -r")

    assert unknown == []
    assert passed == ["--model"]


def test_given_both_spellings_of_a_declared_option_when_classified_then_both_resolve():
    assert classify("ai sync push -n") == ([], [])
    assert classify("ai sync push --dry-run") == ([], [])
    assert classify("ai doctor [-n/--dry-run]") == ([], [])


def test_given_an_exempt_marker_when_examples_are_read_then_the_line_is_skipped_and_counted(tmp_path):
    doc = tmp_path / "guide.md"
    doc.write_text(f"```bash\nai nosuch  # {EXEMPT}\nai ls\n```\n", encoding="utf-8")

    lines = [line for _, line in _example_lines(doc)]

    assert [line for line in lines if EXEMPT in line] == [f"ai nosuch  # {EXEMPT}"]
    assert [classify(line)[0] for line in lines if EXEMPT not in line] == [[]]


# --- T-2.5 hand-written docs point at the reference --------------------------------------


def test_given_the_cli_guide_when_read_then_it_links_to_the_reference_at_the_top_and_has_no_flag_table():
    text = (REPO / "docs/tools/ai-cli-usage.md").read_text(encoding="utf-8")
    top = text.split("\n## ", 1)[0]
    flag_rows = [line for line in text.splitlines() if line.startswith("|") and re.match(r"\|\s*`?-{1,2}\w", line)]

    assert "../reference/cli.md" in top
    assert flag_rows == []


@pytest.mark.parametrize("name", ["CLAUDE-full.md", "AGENTS.md"])
def test_given_the_contributor_instructions_when_read_then_they_name_the_regenerate_command(name):
    assert "scripts/generate_docs.py" in (REPO / name).read_text(encoding="utf-8")
