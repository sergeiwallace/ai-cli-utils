"""Every visible option of every ``ai`` command has a short form beside its long one."""

import click

from ai_cli.main import _cli_group
from scripts.generate_docs import walk


def _visible_options(group):
    for entry in walk(group):
        for param in entry.command.get_params(entry.ctx):
            if isinstance(param, click.Option) and not param.hidden:
                yield entry.path, param


def _long_only_options(group):
    return [
        f"{command} {param.opts}"
        for command, param in _visible_options(group)
        if not any(opt.startswith("-") and not opt.startswith("--") for opt in param.opts + param.secondary_opts)
    ]


def test_given_every_ai_command_when_options_are_listed_then_none_is_long_only():
    long_only = _long_only_options(_cli_group)

    assert long_only == []


def test_given_a_long_only_root_option_when_options_are_listed_then_the_root_violation_is_reported():
    @click.group(context_settings={"help_option_names": ["-h", "--help"]})
    @click.option("--probe-only")
    def root(probe_only):
        del probe_only

    assert _long_only_options(root) == ["root ['--probe-only']"]
