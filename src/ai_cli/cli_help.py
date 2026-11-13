"""Command text shared by ``ai -h`` and the generated CLI reference.

The help written in each command's Click declaration is the only source of its
description. :func:`command_description` turns it into the text both the help
screens and ``scripts/generate_docs.py`` print, so the two cannot disagree.
:data:`START_RESUME_ATTACH` is the one source of the "Start, resume, attach" rows
``ai -h``, the README and the CLI guide all print.
"""

import sys
import textwrap

import click

#: The CLI guide's path in the repository; ``ai -h`` ends with its URL.
GUIDE_PATH = "docs/tools/ai-cli-usage.md"

#: How to start, resume and attach to a session: (command, what it does).
START_RESUME_ATTACH = (
    (
        "ai c NAME",
        "Start a Claude Code session named NAME. When NAME's tmux session is already running, attach to it "
        "instead; `ai attach` is not needed for a session `ai` started",
    ),
    ("ai g NAME", "The same for Gemini CLI; `ai p NAME` for Pi and `ai cx NAME` for Codex"),
    (
        "ai c NAME -r",
        "Resume NAME's existing session, running or stopped (NAME is its title, first name or index); refuses, "
        "creating nothing, when no session holds NAME",
    ),
    ("ai c NAME -R -r", "The same on the default remote machine; -m ALIAS picks another configured machine"),
    (
        "ai c -t NAME",
        "Start a throwaway probe session: it holds no first name, its worktree lives outside the repo, it is "
        "never a session home and it is never relaunched at boot",
    ),
    ("ai cos -r", "Resume this machine's chief-of-staff session"),
    (
        "ai sessions",
        "List the running sessions of this host and its paired host; `ai sessions list -a -S fleet` lists every "
        "session of every host that publishes them",
    ),
)

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


def guide_url() -> str:
    """The CLI guide's URL, from the installed distribution's ``Repository`` project URL."""
    from importlib import metadata

    try:
        entries = metadata.metadata("ai-cli-utils").get_all("Project-URL") or []
    except metadata.PackageNotFoundError:
        entries = []
    for entry in entries:
        label, _, url = entry.partition(",")
        if label.strip().casefold() == "repository" and url.strip():
            return f"{url.strip().rstrip('/')}/blob/main/{GUIDE_PATH}"
    return GUIDE_PATH


def command_tree(group: click.Group, ctx: click.Context) -> list[tuple[int, str, click.Command]]:
    """Every visible command below ``group`` as (depth, name, command), each group followed by its own."""
    rows = []
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        if command is None or command.hidden:
            continue
        rows.append((0, name, command))
        if isinstance(command, click.Group):
            sub_ctx = click.Context(command, info_name=name, parent=ctx)
            rows += [(depth + 1, sub, cmd) for depth, sub, cmd in command_tree(command, sub_ctx)]
    return rows


class RootHelpGroup(HelpGroup):
    """The root group: its help lists the whole command tree, one line per command.

    Before the options it prints how to start, resume and attach, and it ends with the
    CLI guide's URL. Each group's own help keeps the complete descriptions.
    """

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_usage(ctx, formatter)
        self.format_help_text(ctx, formatter)
        with formatter.section("Start, resume, attach"):
            formatter.write_dl(list(START_RESUME_ATTACH))
        self.format_options(ctx, formatter)
        self.format_epilog(ctx, formatter)
        formatter.write_paragraph()
        formatter.write(f"Full CLI guide: {guide_url()}\n")

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows = [("  " * depth + name, command_summary(command)) for depth, name, command in command_tree(self, ctx)]
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)
