#!/usr/bin/env python3
"""Generate the CLI reference and the README command index from the ``ai`` command tree.

The help written in each command's Click declaration is the only source. This script
renders it into ``docs/reference/cli.md`` (every byte generated) and into the region of
``README.md`` between the ``cli-index`` markers (no other byte touched). With
``-c/--check`` it writes nothing and exits 1 with a diff when either file is stale.

Usage:
  uv run python scripts/generate_docs.py           # rewrite both files
  uv run python scripts/generate_docs.py -c        # check only
"""

import argparse
import difflib
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import click

REPO = Path(__file__).resolve().parents[1]
REFERENCE = "docs/reference/cli.md"
README = "README.md"
BEGIN = "<!-- cli-index:begin -->"
END = "<!-- cli-index:end -->"
REGENERATE = "uv run python scripts/generate_docs.py"


class GenerateError(Exception):
    """A source file the generator cannot update safely."""


@dataclass(frozen=True)
class Entry:
    path: str
    command: click.Command
    ctx: click.Context


def _context(command: click.Command, name: str, parent: click.Context | None) -> click.Context:
    return click.Context(command, info_name=name, parent=parent, **command.context_settings)


def walk(root: click.Group) -> Iterator[Entry]:
    """Every visible command, the root first, in the order ``-h`` lists them."""

    def visit(command: click.Command, name: str, parent: click.Context | None) -> Iterator[Entry]:
        ctx = _context(command, name, parent)
        yield Entry(ctx.command_path, command, ctx)
        if isinstance(command, click.Group):
            for sub_name in command.list_commands(ctx):
                sub = command.get_command(ctx, sub_name)
                if sub is not None and not sub.hidden:
                    yield from visit(sub, sub_name, ctx)

    yield from visit(root, root.name or "ai", None)


def anchor(path: str) -> str:
    """The heading anchor GitHub gives ``## <path>`` for a command path."""
    return path.lower().replace(" ", "-")


def _cell(text: str) -> str:
    return _inline(text).replace("|", "\\|").replace("\n", " ")


def _inline(text: str) -> str:
    # A bare <word> outside a code span would be read as an HTML tag and vanish from the page.
    parts = re.split(r"(`[^`]*`)", text)
    return "".join(part if part.startswith("`") else part.replace("<", "&lt;") for part in parts)


def _code(text: str) -> str:
    return f"`{text}`" if text else ""


def render_index(root: click.Group, link_base: str) -> str:
    """A table of every visible command below the root and its summary, linking to its section."""
    from ai_cli.cli_help import command_summary

    lines = ["| Command | Summary |", "|---|---|"]
    for entry in walk(root):
        if entry.ctx.parent is None:
            continue
        lines.append(
            f"| [`{entry.path}`]({link_base}#{anchor(entry.path)}) | {_cell(command_summary(entry.command))} |"
        )
    return "\n".join(lines) + "\n"


def _description(entry: Entry) -> list[str]:
    from ai_cli.cli_help import command_description, paragraphs

    out: list[str] = []
    for part in paragraphs(command_description(entry.command, entry.ctx)):
        if "\n" in part:
            out += ["```text", part, "```", ""]
        else:
            out += [_inline(part), ""]
    return out


def _arguments(entry: Entry) -> list[str]:
    rows = []
    for param in entry.command.get_params(entry.ctx):
        if isinstance(param, click.Argument):
            help_text = getattr(param, "help", None)
            rows.append(f"- `{param.make_metavar(entry.ctx)}`" + (f": {_inline(help_text)}" if help_text else ""))
    return ["Arguments:", "", *rows, ""] if rows else []


def _options(entry: Entry) -> list[str]:
    rows = []
    for param in entry.command.get_params(entry.ctx):
        if not isinstance(param, click.Option):
            continue
        record = param.get_help_record(entry.ctx)
        if record is None:
            continue
        names = [*param.opts, *param.secondary_opts]
        short = ", ".join(_code(name) for name in names if not name.startswith("--"))
        long = ", ".join(_code(name) for name in names if name.startswith("--"))
        value = "" if param.is_flag or param.count else _code(param.make_metavar(entry.ctx)).replace("|", "\\|")
        rows.append(f"| {short} | {long} | {value} | {_cell(record[1])} |")
    if not rows:
        return []
    return ["| Short | Long | Value | Description |", "|---|---|---|---|", *rows, ""]


def render_reference(root: click.Group) -> str:
    """The full content of ``docs/reference/cli.md``."""
    entries = list(walk(root))
    anchors = [anchor(entry.path) for entry in entries]
    duplicates = sorted({a for a in anchors if anchors.count(a) > 1})
    if duplicates:
        raise GenerateError(f"command paths share a heading anchor: {', '.join(duplicates)}")
    lines = [
        "# CLI reference",
        "",
        f"<!-- Generated by `{REGENERATE}` from the command declarations in src/. Do not edit by hand. -->",
        "",
        "Every `ai` command, its usage line, its description and every option in both its short and",
        "long form, exactly as `ai <command> -h` prints them. For walk-throughs and configuration, see",
        "the [CLI guide](../tools/ai-cli-usage.md).",
        "",
        "## Commands",
        "",
        render_index(root, "").rstrip("\n"),
        "",
    ]
    for entry in entries:
        usage = " ".join([entry.path, *entry.command.collect_usage_pieces(entry.ctx)])
        lines += [f"## {entry.path}", "", "```text", usage, "```", ""]
        lines += _description(entry) + _arguments(entry) + _options(entry)
    return "\n".join(lines).rstrip("\n") + "\n"


def replace_region(text: str, region: str, name: str) -> str:
    """``text`` with only the bytes between the markers replaced by ``region``."""
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
        raise GenerateError(f"{name}: needs exactly one {BEGIN} followed by exactly one {END}")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{region}{END}{tail}"


def _root() -> click.Group:
    from ai_cli.main import _cli_group

    return _cli_group


def expected(repo: Path, root: click.Group) -> dict[str, str]:
    """Each generated file's path and the content it should have."""
    readme = (repo / README).read_text(encoding="utf-8")
    return {
        REFERENCE: render_reference(root),
        README: replace_region(readme, render_index(root, REFERENCE), README),
    }


def stale(repo: Path, root: click.Group) -> str:
    """A unified diff of every generated file that differs from what it should be, or ``""``."""
    diffs = []
    for rel, content in expected(repo, root).items():
        path = repo / rel
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != content:
            diffs += difflib.unified_diff(
                current.splitlines(keepends=True), content.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"
            )
    return "".join(diffs)


def write(repo: Path, root: click.Group) -> list[str]:
    """Write every generated file that changed; return their paths."""
    changed = []
    for rel, content in expected(repo, root).items():
        path = repo / rel
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
            changed.append(rel)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "-c", "--check", action="store_true", help="write nothing; exit 1 with a diff when a file is stale"
    )
    args = parser.parse_args(argv)
    root = _root()
    try:
        if args.check:
            diff = stale(REPO, root)
            if diff:
                sys.stdout.write(diff)
                print(f"\nGenerated docs are stale. Run: {REGENERATE}", file=sys.stderr)
                return 1
            return 0
        for rel in write(REPO, root):
            print(f"wrote {rel}")
    except GenerateError as exc:
        print(f"generate_docs: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
