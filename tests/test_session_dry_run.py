"""`ai c --dry-run` must resolve a plan and create nothing (AI-CLI-wepi).

THE BUG. `--dry-run` was documented for `ai c`/`ai g` in
docs/tools/ai-cli-usage.md and never implemented. The session commands set
`ignore_unknown_options`/`allow_extra_args` so that engine flags can be
forwarded, which meant an undeclared `--dry-run` was silently swallowed into
`ctx.args` and passed to the engine -- and the launch ran for real. Measured:
`ai c 99 --dry-run` created worktree `.worktrees/ai-cli-99`, branch
`wt-ai-cli-99`, wrote a Claude Code version lock inside it, and only stopped at
`open terminal failed: not a terminal` because the caller had no TTY. On a TTY it
would have started a session.

A flag whose entire promise is "do nothing" silently doing everything is the
worst available failure, so the declaration itself is the fix and these tests
guard it from both sides: the option must exist, and it must not mutate.
"""

from unittest.mock import patch

import pytest
from conftest import run_cli

from ai_cli.main import _do_session_launch, _session_options


def _declared_option_names():
    """The click params `_session_options` actually declares."""

    def _target(*_args, **_kwargs):
        return None

    decorated = _session_options(_target)
    return {name for param in decorated.__click_params__ for name in getattr(param, "opts", [])}


def test_dry_run_is_a_declared_option_not_an_engine_passthrough():
    """The whole defect in one assertion.

    With ignore_unknown_options set, an undeclared flag is not rejected -- it is
    forwarded. So "the CLI accepted --dry-run" proves nothing; only its presence
    among the declared params does.
    """
    assert "--dry-run" in _declared_option_names()


def test_dry_run_appears_in_help_so_it_is_discoverable():
    code, out, _err = run_cli(["ai", "c", "--help"])
    assert code == 0
    assert "--dry-run" in out


class _MutationTripwire(AssertionError):
    pass


def _explode(*_args, **_kwargs):
    raise _MutationTripwire("a dry run reached a mutating call")


def test_dry_run_reaches_no_mutating_call_and_prints_the_resolved_plan(capsys):
    """Tripwires on every write the launch would perform after name resolution.

    Asserting "no worktree appeared" would pass for the wrong reason if the code
    exited early for an unrelated reason, so each mutation is replaced by a
    raising stub instead: reaching any of them is the failure.
    """
    with (
        patch("ai_cli.session.create_worktree", side_effect=_explode),
        patch("ai_cli.session.cleanup_stale_sessions", side_effect=_explode),
        patch("ai_cli.trust.ensure_workspace_trusted", side_effect=_explode),
        patch("os.execvp", side_effect=_explode),
        # NOT subprocess.run: the plan itself asks git for the repo root, and a
        # read-only probe is not a mutation. Tripwiring every sub-process
        # conflated "spawns a process" with "changes something" and failed this
        # test against correct code.
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="test"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.build_session_name", return_value=("c-test-7", "test-7")),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
    ):
        _do_session_launch(
            engine="c",
            name="7",
            resume=False,
            once=False,
            bare=False,
            notify=False,
            sandbox=False,
            no_worktree=False,
            remote=False,
            project="",
            is_remote=False,
            project_prefix_override="test",
            extra_args=[],
            config={},
            dry_run=True,
        )

    out = capsys.readouterr().out
    assert "dry run" in out
    # The resolved values are the point: a dry run that echoed the command line
    # back would be useless.
    assert "test-7" in out
    assert "tmux" in out


def test_a_dry_run_in_bare_mode_does_not_sweep_sessions(capsys):
    """The sweep reaps dead sessions, so it is a mutation even when it looks idle."""
    with (
        patch("ai_cli.session.cleanup_stale_sessions", side_effect=_explode),
        patch("ai_cli.session.create_worktree", side_effect=_explode),
        patch("ai_cli.trust.ensure_workspace_trusted", side_effect=_explode),
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="test"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.build_session_name", return_value=("c-test-8", "test-8")),
    ):
        _do_session_launch(
            engine="c",
            name="8",
            resume=False,
            once=False,
            bare=True,
            notify=False,
            sandbox=False,
            no_worktree=False,
            remote=False,
            project="",
            is_remote=False,
            project_prefix_override="test",
            extra_args=[],
            config={},
            dry_run=True,
        )
    assert "bare" in capsys.readouterr().out


def test_without_dry_run_the_launch_still_reaches_its_work():
    """Anti-vacuity control: the tripwires above must be reachable at all.

    Without this, deleting the whole launch body would make every test in this
    file pass.
    """
    with (
        patch("ai_cli.session.create_worktree", side_effect=_explode),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="test"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.build_session_name", return_value=("c-test-9", "test-9")),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
        patch("ai_cli.main.repair_bare_worktree_config"),
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(_MutationTripwire):
            _do_session_launch(
                engine="c",
                name="9",
                resume=False,
                once=False,
                bare=False,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="test",
                extra_args=[],
                config={},
                dry_run=False,
            )
