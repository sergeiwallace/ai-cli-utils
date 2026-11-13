"""``ai -h`` lists the whole command tree; every group's own help lists each command's complete description."""

import os
import re
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner
from conftest import run_cli

from ai_cli.cli_help import GUIDE_PATH, START_RESUME_ATTACH, HelpGroup, command_description, command_summary
from ai_cli.main import _cli_group


def _walk(group, path):
    for name in group.list_commands(None):
        command = group.commands[name]
        if command.hidden:
            continue
        yield [*path, name], command
        if isinstance(command, click.Group):
            yield from _walk(command, [*path, name])


def _groups():
    return [([], _cli_group)] + [
        (path, command) for path, command in _walk(_cli_group, []) if isinstance(command, click.Group)
    ]


def _flatten(text):
    """Undo Click's wrapping: rejoin words split after a hyphen, then collapse whitespace."""
    return " ".join(re.sub(r"(?<=\w-)\n\s+", "", text).split())


def _commands_section(help_text):
    return help_text.split("\nCommands:\n", 1)[1]


def _help(path):
    result = CliRunner().invoke(_cli_group, [*path, "-h"], terminal_width=80)
    assert result.exit_code == 0, result.output
    return result.output


def _ctx(command):
    return click.Context(command, info_name=command.name, help_option_names=["-h", "--help"])


def tree_paths(help_text):
    """The command paths the root help's command tree lists, rebuilt from each name's indentation."""
    section = _commands_section(help_text).split("\n\n", 1)[0]
    stack, paths = [], []
    for line in section.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if indent > 12 or not line.strip():
            continue
        depth = (indent - 2) // 2
        stack[depth:] = [line.split()[0]]
        paths.append(" ".join(stack))
    return paths


@pytest.mark.parametrize(
    ("path", "group"), _groups()[1:], ids=lambda value: " ".join(value) if isinstance(value, list) else ""
)
def test_given_a_group_when_its_help_is_rendered_at_width_80_then_every_subcommand_description_is_printed_whole(
    path, group
):
    output = _help(path)
    listed = _flatten(_commands_section(output))
    missing = [
        name
        for name in group.list_commands(None)
        if not group.commands[name].hidden
        and _flatten(command_description(group.commands[name], _ctx(group.commands[name]))) not in listed
    ]
    truncated = [line for line in _commands_section(output).splitlines() if line.rstrip().endswith("...")]

    assert missing == []
    assert truncated == []


def test_given_the_root_help_when_rendered_then_every_command_and_subcommand_is_listed_with_its_summary():
    output = _help([])
    expected = [" ".join(path) for path, _ in _walk(_cli_group, [])]
    tree = _commands_section(output)
    listed = _flatten(tree)
    unsummarised = [
        " ".join(path) for path, command in _walk(_cli_group, []) if _flatten(command_summary(command)) not in listed
    ]

    assert tree_paths(output) == expected
    assert "sessions list" in expected and "sessions publish-timer install" in expected
    assert unsummarised == []
    assert [line for line in tree.splitlines() if line.rstrip().endswith("...")] == []


def test_given_the_root_help_when_rendered_then_it_shows_how_to_start_resume_and_attach_before_the_options():
    output = _help([])
    section = output.split("Start, resume, attach:\n", 1)[1].split("\nOptions:\n", 1)[0]

    assert [_flatten(text) in _flatten(section) for _, text in START_RESUME_ATTACH] == [True] * len(START_RESUME_ATTACH)
    assert [command in section for command, _ in START_RESUME_ATTACH] == [True] * len(START_RESUME_ATTACH)
    assert "ai c NAME -r" in section and "ai cos -r" in section
    assert "`ai attach` is not needed" in _flatten(section)


def test_given_the_root_help_when_rendered_then_its_last_line_is_the_cli_guide_url():
    last = _help([]).rstrip("\n").splitlines()[-1]

    assert last.startswith("Full CLI guide: https://github.com/")
    assert last.endswith(f"/blob/main/{GUIDE_PATH}")


def test_given_a_command_with_short_help_when_summarised_then_the_summary_is_short_help_but_the_list_prints_the_description():
    group = HelpGroup("tool")

    @group.command("act", short_help="\n    Act now.\n    ", help="Act now on every item. Then report what changed.")
    def act():
        pass

    result = CliRunner().invoke(group, ["--help"])

    assert command_summary(act) == "Act now."
    assert "Act now on every item. Then report what changed." in result.output


def test_given_a_command_with_no_help_when_described_then_description_and_summary_are_empty():
    command = click.Command("bare")

    assert command_description(command, _ctx(command)) == ""
    assert command_summary(command) == ""


def test_given_a_120_column_terminal_when_root_help_is_rendered_then_content_width_is_100():
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((120, 40))):
        with _cli_group.make_context("ai", [], resilient_parsing=True) as ctx:
            text = ctx.get_help()
            width = ctx.make_formatter().width

    longest = max(len(line) for line in text.splitlines())
    assert ctx.max_content_width == 100
    assert width == 98
    assert 80 < longest <= 100


def test_given_a_configured_chief_alias_when_invoked_then_it_routes_to_cos():
    with patch("ai_cli.main._chief_alias_setting", return_value=("boss",)):
        alias = CliRunner().invoke(_cli_group, ["boss", "-h"])
    cos = CliRunner().invoke(_cli_group, ["cos", "-h"])

    assert alias.exit_code == 0
    assert alias.output.splitlines()[1:] == cos.output.splitlines()[1:]


def test_given_no_arguments_when_ai_runs_then_it_prints_the_same_help_as_ai_h():
    bare = run_cli(["ai"])
    flagged = run_cli(["ai", "-h"])

    assert bare[0] == flagged[0] == 0
    assert bare[1] == flagged[1]
    assert "Commands:" in bare[1]


def test_given_the_command_tree_when_walked_then_every_visible_command_has_a_summary():
    unsummarised = [" ".join(path) for path, command in _walk(_cli_group, []) if not command_summary(command)]

    assert unsummarised == []


def test_given_the_command_tree_when_walked_then_every_group_uses_the_shared_help_class():
    plain = [" ".join(path) or "ai" for path, group in _groups() if not isinstance(group, HelpGroup)]

    assert plain == []
