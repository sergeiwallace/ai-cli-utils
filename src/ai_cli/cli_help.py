"""Command text shared by ``ai -h`` and the generated CLI reference.

The help written in each command's Click declaration is the only source of its
description. :func:`command_description` turns it into the text both the help
screens and ``scripts/generate_docs.py`` print, so the two cannot disagree.
"""

import sys
import textwrap

import click

# Wide enough that wrapping never splits an ordinary paragraph: each one comes back
# as a single line, while a ``\b`` paragraph keeps the line breaks it was written with.
_UNWRAPPED = 1_000_000


def command_description(command: click.Command, ctx: click.Context) -> str:
    """The command's complete help text, formatted by Click's own rules, or ``""``.

    Click's cleanup applies: indentation is removed, text after ``\\f`` is dropped and a
    deprecation label is appended. Paragraphs are separated by a blank line; an
    ordinary paragraph is one line and a ``\\b`` paragraph keeps its own line breaks.
    """
    formatter = click.HelpFormatter(width=_UNWRAPPED, max_width=_UNWRAPPED)
    command.format_help_text(ctx, formatter)
    return textwrap.dedent(formatter.getvalue()).strip("\n")


def command_summary(command: click.Command) -> str:
    """A one-line label for compact indexes: ``short_help`` when set, else the first sentence."""
    return command.get_short_help_str(limit=sys.maxsize)


def paragraphs(description: str) -> list[str]:
    """Split a :func:`command_description` into its paragraphs."""
    return [part for part in description.split("\n\n") if part]


def _for_definition_list(description: str) -> str:
    # write_dl rewraps every paragraph unless it carries Click's no-rewrap marker.
    return "\n\n".join(f"\b\n{part}" if "\n" in part else part for part in paragraphs(description))


class HelpGroup(click.Group):
    """A group whose command list prints each command's complete description.

    Click's default list prints the first sentence cut to fit one line. Every group
    in the ``ai`` tree uses this class; subgroups inherit it through ``group_class``.
    """

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows = []
        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            rows.append((name, _for_definition_list(command_description(command, ctx))))
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


HelpGroup.group_class = HelpGroup
