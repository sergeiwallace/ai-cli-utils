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

from unittest.mock import MagicMock, patch

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


def test_dry_run_remote_reaches_no_network_call_and_prints_the_resolved_plan(capsys):
    """The remote counterpart of the tripwire test above (AI-CLI-shpu).

    ``--remote --dry-run`` used to reach a real SSH shell probe, a real ``ai
    update`` on the remote host, real session allocation, and a real SSH/mosh
    handoff -- confirmed live via an actual ``ai c 4 -R --dry-run`` that created
    a real tmux session on a real remote host. Tripwire every network call
    instead of asserting on one specific call, so reaching ANY of them is the
    failure.
    """
    config = {"remote": {"host": "fw.example.com", "user": "dev", "port": 22, "identity_file": "", "transport": "ssh"}}
    with (
        patch("ai_cli.main.subprocess.run", side_effect=_explode),
        patch("os.execvp", side_effect=_explode),
    ):
        _do_session_launch(
            engine="c",
            name="1",
            resume=False,
            once=False,
            bare=False,
            notify=False,
            sandbox=False,
            no_worktree=False,
            remote=True,
            project="",
            is_remote=False,
            project_prefix_override="test",
            extra_args=[],
            config=config,
            no_direnv=True,
            dry_run=True,
        )

    out = capsys.readouterr().out
    assert "dry run -- nothing was created, started, or reaped." in out
    # The resolved values are the point, mirroring the local plan's contract.
    assert "target host  dev@fw.example.com:22 (ssh)" in out
    assert "remote cmd" in out


def test_without_dry_run_the_remote_launch_still_reaches_the_network():
    """Anti-vacuity control for the remote tripwire test above.

    Without this, deleting the remote dry-run check entirely -- or misplacing
    it below the network calls instead of above them -- would make the tripwire
    test above pass for the wrong reason.

    Not a raising tripwire like the local-path control below: the remote
    preflight calls (``_resolve_remote_shell``, ``_update_remote_ai_cli``)
    deliberately swallow every exception from ``subprocess.run`` and degrade
    instead of raising, so a raising side_effect here would be silently
    absorbed and the launch would fall through to a REAL ``os.execvp`` --
    exactly the hang this bug's own regression test caused once already.
    Reachability is proven by call evidence instead.
    """
    config = {"remote": {"host": "fw.example.com", "user": "dev", "port": 22, "identity_file": "", "transport": "ssh"}}
    with (
        # The SSH transport refuses outright on Windows and exits 1 before it
        # reaches the handoff patched below, so without this the control asserted a
        # POSIX-only path: `pytest.raises(SystemExit)` was satisfied by the refusal and
        # `mock_exec.called` was then False, failing the whole test-windows job.
        # Forcing the branch is the repo's established pattern for a
        # platform-dependent path (test_remote.py does the mirror image to reach
        # the Windows refusal from POSIX), and it is the stronger fix here: the
        # ordering this control exists to prove is not platform-specific, so it
        # should be proven on every platform rather than skipped on one. Nothing
        # between here and that handoff branches on the platform -- the local tmux
        # preflight that does is already skipped for a remote launch.
        # Turned `main` red on test-windows at a274bcb, and two sessions fixed it the same
        # way concurrently -- which is how this line came to be applied twice.
        patch("ai_cli.main.sys.platform", "linux"),
        patch(
            "ai_cli.main.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="bash\n", stderr=""),
        ) as mock_run,
        # The pure-SSH handoff is `transport.run_ssh_with_reconnect`, not an exec:
        # that path runs in-process now so a dropped link can be reattached
        # (AI-CLI-w679). What this control proves is unchanged -- without
        # --dry-run the launch still reaches the network -- only the boundary
        # that evidences "reached it" moved.
        patch("ai_cli.transport.run_ssh_with_reconnect", side_effect=SystemExit(0)) as mock_exec,
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(
                engine="c",
                name="1",
                resume=False,
                once=False,
                bare=False,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=True,
                project="",
                is_remote=False,
                project_prefix_override="test",
                extra_args=[],
                config=config,
                no_direnv=True,
                dry_run=False,
            )
    assert mock_run.called
    assert mock_exec.called


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
