"""Every visible option of every ``ai`` command has a short form beside its long one."""

import click

from ai_cli.main import _cli_group


def _visible_options(group, path):
    for name, command in group.commands.items():
        if isinstance(command, click.Group):
            yield from _visible_options(command, [*path, name])
        for param in command.params:
            if isinstance(param, click.Option) and not param.hidden:
                yield " ".join([*path, name]), param


def test_given_every_ai_command_when_options_are_listed_then_none_is_long_only():
    long_only = [
        f"ai {command} {param.opts}"
        for command, param in _visible_options(_cli_group, [])
        if not any(opt.startswith("-") and not opt.startswith("--") for opt in param.opts + param.secondary_opts)
    ]

    assert long_only == []
