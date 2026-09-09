"""A tmux that resolves but cannot execute must not be treated as usable.

THE BUG (AI-CLI-i2ih, AI-CLI-d89q). On a machine whose container filesystem is
rebuilt on restart, a tmux living in the persistent per-user prefix lost the
shared library it had been linked against::

    $ tmux -V
    tmux: error while loading shared libraries: libevent_core-2.1.so.7:
    cannot open shared object file: No such file or directory
    exit=127

`ai c` then printed ``tmux version unavailable (binary does not run)``,
immediately followed by ``launching inside tmux``, synchronized a worktree, and
died on that same loader error at ``tmux new-session``. Three separate places
conflated presence with runnability:

* ``ensure_tmux`` returned ``installed=True, tool='already-present'`` from a
  bare ``tmux_present()`` check, so no repair or install was ever attempted;
* ``install_tmux`` verified its work with ``tmux_present`` too, so a manager
  that produced an unrunnable tmux read as a success;
* the launcher had the answer already — ``probe().runs`` was ``False`` — and
  nothing consulted it.

The library itself was still on the box, in a persistent directory, and tmux ran
the moment the loader was pointed at it. So the fix is: assert execution, repair
the loader path from what is already there, and if that cannot be done fall back
to bare mode *before* anything is created.

``native_deps``' side of the repair is tested against a real child process and a
real loader in tests/test_native_deps.py. This file covers the tmux-specific
decisions layered on top, and the launch that has to act on them.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from ai_cli import tmux_setup
from ai_cli.native_deps import InstallResult, LoaderRepair

MISSING_LIB = "libevent_core-2.1.so.7"
LIB_DIR = "/home/user/.local/lib/tmux-appimage/usr/lib"


def _repaired() -> LoaderRepair:
    return LoaderRepair(
        repaired=True,
        missing=(MISSING_LIB,),
        added_dirs=(LIB_DIR,),
        variable="LD_LIBRARY_PATH",
    )


def _unrepairable() -> LoaderRepair:
    """tmux names a library, and it is nowhere. Positive evidence of unusable."""
    return LoaderRepair(
        repaired=False,
        missing=(MISSING_LIB,),
        unresolved=(MISSING_LIB,),
        detail=f"{MISSING_LIB} not found in any of: /home/user/.local/lib",
    )


# ---------------------------------------------------------------------------
# ensure_tmux: execution, then repair, then install
# ---------------------------------------------------------------------------


class TestEnsureTmuxAssertsExecution:
    def test_given_a_present_but_unrunnable_tmux_when_ensured_then_a_repair_is_attempted(self):
        """The regression in one assertion: presence alone used to end the story."""
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", return_value=_repaired()) as repair,
            patch("ai_cli.tmux_setup.install_tmux") as install,
        ):
            result = tmux_setup.ensure_tmux(quiet=True)

        repair.assert_called_once()
        install.assert_not_called()
        assert result.installed is True
        assert result.tool == "loader-path"
        assert MISSING_LIB in result.detail
        assert LIB_DIR in result.detail

    def test_given_a_runnable_tmux_when_ensured_then_neither_repair_nor_install_runs(self):
        """The healthy path must stay free of both: a repair probe on every launch
        of a working machine is pure cost, and an install is worse."""
        with (
            patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path") as repair,
            patch("ai_cli.tmux_setup.install_tmux") as install,
        ):
            result = tmux_setup.ensure_tmux(quiet=True)

        repair.assert_not_called()
        install.assert_not_called()
        assert result.installed is True
        assert result.tool == "already-present"

    def test_given_an_unrepairable_tmux_when_ensured_then_no_install_is_attempted(self):
        """A package manager is not a remedy for a tmux that is already here.

        It cannot be verified to have fixed the tmux that will actually run — the
        broken binary keeps its place on PATH — so the launch would pay the
        manager's full timeout, up to five minutes on EVERY launch, to change
        nothing. It is also an unrequested machine mutation, installing a second
        tmux over one the operator placed by hand. Bare mode plus a notice naming
        the missing library is the honest outcome.

        (This inverts the contract as first written here. The full suite caught 32
        launch tests entering the install path mid-launch, which is what surfaced
        the cost of the version that did try.)
        """
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", return_value=_unrepairable()),
            patch("ai_cli.tmux_setup.install_tmux") as install,
        ):
            result = tmux_setup.ensure_tmux(quiet=True)

        install.assert_not_called()
        assert result.installed is False
        assert result.unusable is True, "a named, unresolvable library IS positive evidence"
        assert MISSING_LIB in result.detail

    def test_given_tmux_answers_no_version_but_names_no_library_then_it_is_not_called_unusable(self):
        """ "Could not confirm" is not "broken", and only one of them may cost the
        operator detach/reattach.

        A tmux that runs but prints an unexpected `-V` shape lands here. Reporting
        it unusable would degrade every launch on such a host to bare mode over a
        parsing quirk — a worse bug than the loader failure this preflight exists
        to catch. So no remediation is printed either: nothing has been
        established to remediate.
        """
        ambiguous = LoaderRepair(repaired=False, detail="tmux already runs; nothing to repair")
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", return_value=ambiguous),
            patch("ai_cli.tmux_setup.install_tmux") as install,
        ):
            result = tmux_setup.ensure_tmux(quiet=True)

        install.assert_not_called()
        assert result.installed is False
        assert result.unusable is False

    def test_given_an_absent_tmux_when_ensured_then_an_install_is_attempted(self):
        """The one case a manager can actually answer: there is no tmux at all."""
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=False),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(True, tool="micromamba")) as install,
        ):
            result = tmux_setup.ensure_tmux(quiet=True)

        install.assert_called_once()
        assert result.installed is True
        assert result.tool == "micromamba"

    def test_given_an_absent_tmux_when_ensured_then_no_repair_is_attempted(self):
        """There is no loader path that fixes a binary that is not there, and
        probing for one would just slow the honest install attempt down."""
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=False),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path") as repair,
            patch("ai_cli.tmux_setup.install_tmux", return_value=InstallResult(False, detail="nothing")),
        ):
            tmux_setup.ensure_tmux(quiet=True)

        repair.assert_not_called()

    def test_given_nothing_works_when_ensured_then_the_notice_names_the_missing_library(self, capsys):
        """AC-2. 'tmux is not usable here' sends an operator nowhere; the soname,
        the directories that were searched, and the override that fixes it are
        the whole diagnosis."""
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", return_value=_unrepairable()),
        ):
            result = tmux_setup.ensure_tmux()

        assert result.installed is False
        err = capsys.readouterr().err
        assert MISSING_LIB in err
        assert "AI_CLI_LIBRARY_PATH" in err
        assert "bare mode" in err

    def test_given_auto_install_is_off_when_ensured_then_a_repair_is_still_attempted(self):
        """``auto_install=False`` refuses to touch the machine's packages. The
        loader repair installs nothing at all, so it is not what was declined."""
        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", return_value=_repaired()) as repair,
            patch("ai_cli.tmux_setup.install_tmux") as install,
        ):
            result = tmux_setup.ensure_tmux(auto_install=False, quiet=True)

        repair.assert_called_once()
        install.assert_not_called()
        assert result.installed is True


class TestInstallVerifiesByExecution:
    def test_given_a_manager_that_lands_an_unrunnable_tmux_when_installed_then_it_is_a_failure(self):
        """AC-1, and the AI-CLI-d89q conflation at its source: ``which tmux``
        succeeding is not evidence that a single session can start."""
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda tool: "/usr/bin/conda" if tool == "conda" else None),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
        ):
            result = tmux_setup.install_tmux()

        assert result.installed is False

    def test_given_a_manager_that_lands_a_runnable_tmux_when_installed_then_it_succeeds(self):
        """Anti-vacuity control for the case above."""
        with (
            patch.object(sys, "platform", "linux"),
            patch("shutil.which", side_effect=lambda tool: "/usr/bin/conda" if tool == "conda" else None),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
        ):
            result = tmux_setup.install_tmux()

        assert result.installed is True
        assert result.tool == "conda"


class TestRepairTmuxLoaderPath:
    def test_given_a_repair_request_when_it_runs_then_it_probes_tmux_with_a_read_only_argv(self):
        """``tmux -V`` creates no server, no session and no window. Anything that
        did would make a diagnostic into a mutation."""
        with patch("ai_cli.native_deps.repair_loader_path", return_value=_repaired()) as repair:
            assert tmux_setup.repair_tmux_loader_path().repaired is True

        assert repair.call_args[0][0] == ["tmux", "-V"]


# ---------------------------------------------------------------------------
# The launch decision
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tmux(monkeypatch):
    """Drive PATH and the version answer independently, without a real tmux.

    Patches ``_probe_output`` — the module's own one-command seam — rather than
    the global ``subprocess.run``, for the reason spelled out in
    tests/test_tmux_launch_report.py: a second patch over that global can
    restore in an order that leaves the suite's guard installed past the test.
    """
    state = {"which": "/home/user/.local/bin/tmux", "client": None, "server": None}

    monkeypatch.setattr(tmux_setup.shutil, "which", lambda name: state["which"] if name == "tmux" else None)

    def _probe(argv, timeout):
        if argv[:2] == ["tmux", "-V"]:
            return None if state["client"] is None else f"tmux {state['client']}"
        return state["server"]

    monkeypatch.setattr(tmux_setup, "_probe_output", _probe)
    return state


class _MutationTripwire(RuntimeError):
    pass


def _launch_plan(capsys, **overrides):
    """Resolve a launch plan through ``--dry-run`` and hand back what it printed.

    A dry run reports the RESOLVED tmux-vs-bare decision, which is exactly the
    thing that was wrong, and it reaches no worktree, no session and no exec —
    tripwired below so a regression shows up as a raised error rather than as a
    quietly created session.
    """
    from ai_cli.main import _do_session_launch

    def _explode(*_args, **_kwargs):
        raise _MutationTripwire("a dry run reached a mutating call")

    kwargs = {
        "engine": "c",
        "name": "7",
        "resume": False,
        "once": False,
        "bare": False,
        "notify": False,
        "sandbox": False,
        "no_worktree": False,
        "remote": False,
        "project": "",
        "is_remote": False,
        "project_prefix_override": "test",
        "extra_args": [],
        "config": {},
        "dry_run": True,
    }
    kwargs.update(overrides)

    with (
        patch("ai_cli.session.create_worktree", side_effect=_explode),
        patch("ai_cli.session.cleanup_stale_sessions", side_effect=_explode),
        patch("ai_cli.trust.ensure_workspace_trusted", side_effect=_explode),
        patch("os.execvp", side_effect=_explode),
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="test"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.build_session_name", return_value=("c-test-7", "test-7")),
    ):
        _do_session_launch(**kwargs)
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_given_an_unrepairable_tmux_when_a_launch_resolves_then_the_mode_is_bare(capsys, fake_tmux):
    """The measured failure. Before this the plan said ``tmux``, the launch said
    ``launching inside tmux``, and ``tmux new-session`` died on the loader."""
    with (
        patch("ai_cli.tmux_setup.repair_tmux_loader_path", return_value=_unrepairable()),
        patch("ai_cli.tmux_setup.install_tmux", side_effect=AssertionError("must not install over a present tmux")),
    ):
        out, err = _launch_plan(capsys)

    assert "local, bare" in out
    assert "local, tmux" not in out
    # The operator has to be told which library, or the notice is unactionable.
    assert MISSING_LIB in err

    def _no_tmux_claim(text):
        return "launching inside tmux" not in text

    assert _no_tmux_claim(err), "a broken tmux must never be reported as hosting the session"


def test_given_a_repairable_tmux_when_a_launch_resolves_then_it_stays_under_tmux(capsys, fake_tmux):
    """The point of the repair: the session keeps detach/reattach instead of
    silently losing it. This is also the anti-vacuity control for the case
    above — without it, an implementation that always chose bare would pass."""

    def _repair_and_heal():
        fake_tmux["client"] = "3.7c"
        return _repaired()

    with (
        patch("ai_cli.tmux_setup.repair_tmux_loader_path", side_effect=_repair_and_heal),
        patch("ai_cli.tmux_setup.install_tmux", side_effect=AssertionError("must not install after a repair")),
    ):
        out, err = _launch_plan(capsys)

    assert "local, tmux" in out
    assert "launching inside tmux" in err
    # A loader repair installs nothing. The launch used to report it as
    # "tmux was auto-installed via loader-path", claiming a machine mutation that
    # never happened; the repair's own line names the library and the directory.
    assert "auto-installed" not in err
    assert MISSING_LIB in err
    assert LIB_DIR in err


def test_given_a_bare_launch_when_it_resolves_then_no_repair_is_attempted(capsys, fake_tmux):
    """``--bare`` has already decided tmux is not involved, so it must reach
    neither a version probe nor a repair (tests/test_bare_worktree.py's
    zero-tmux-invocation contract)."""
    with patch("ai_cli.tmux_setup.repair_tmux_loader_path", side_effect=AssertionError("bare probed tmux")):
        out, _ = _launch_plan(capsys, bare=True)

    assert "local, bare" in out


# ---------------------------------------------------------------------------
# Install time
# ---------------------------------------------------------------------------


class TestInstallTimeVerification:
    """AC-1/AC-2: installing or reinstalling must check tmux EXECUTES, and must
    not turn a broken tmux into a failed install."""

    def test_given_setup_runs_when_it_completes_then_tmux_was_verified(self, tmp_path, capsys):
        from ai_cli import setup as ai_setup

        with (
            patch("ai_cli.setup._repo_root_from", return_value=tmp_path),
            patch("ai_cli.setup._is_managed_platform", return_value=True),
            patch("ai_cli.direnv_setup.ensure_direnv", return_value=InstallResult(True)),
            patch("ai_cli.tmux_setup.ensure_tmux", return_value=InstallResult(True, tool="already-present")) as ensure,
        ):
            assert ai_setup.run_setup(tmp_path) == 0

        ensure.assert_called_once()

    def test_given_tmux_is_unusable_when_setup_runs_then_the_install_still_succeeds(self, tmp_path):
        """A missing enhancement must not fail an install. Anything else makes
        ai-cli-utils uninstallable on a box with no tmux at all — including
        every Windows host, where there is no native tmux to have."""
        from ai_cli import setup as ai_setup

        with (
            patch("ai_cli.setup._repo_root_from", return_value=tmp_path),
            patch("ai_cli.setup._is_managed_platform", return_value=True),
            patch("ai_cli.direnv_setup.ensure_direnv", return_value=InstallResult(True)),
            patch("ai_cli.tmux_setup.ensure_tmux", return_value=InstallResult(False, detail=MISSING_LIB)),
        ):
            assert ai_setup.run_setup(tmp_path) == 0

    def test_given_the_tmux_check_itself_explodes_when_setup_runs_then_the_install_survives(self, tmp_path):
        """Belt and braces on the module's non-raising contract: an install that
        dies inside an optional preflight is a worse outcome than a missing tmux."""
        from ai_cli import setup as ai_setup

        with (
            patch("ai_cli.setup._repo_root_from", return_value=tmp_path),
            patch("ai_cli.setup._is_managed_platform", return_value=True),
            patch("ai_cli.direnv_setup.ensure_direnv", return_value=InstallResult(True)),
            patch("ai_cli.tmux_setup.ensure_tmux", side_effect=OSError("boom")),
        ):
            assert ai_setup.run_setup(tmp_path) == 0


# ---------------------------------------------------------------------------
# ai doctor
# ---------------------------------------------------------------------------


class TestDoctorReportsTheRepair:
    def test_given_a_repairable_tmux_when_doctor_runs_then_it_repairs_and_reports_ok(self):
        from conftest import run_cli

        calls = {"n": 0}

        def _runs():
            # Broken on the first look, working once the loader path is fixed.
            return calls["n"] > 0

        def _repair():
            calls["n"] += 1
            return _repaired()

        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", side_effect=_runs),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", side_effect=_repair),
            patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        ):
            _, stdout, _ = run_cli(["ai", "doctor"])

        assert "OK" in stdout
        assert LIB_DIR in stdout

    def test_given_an_unrepairable_tmux_when_doctor_runs_then_it_names_the_library(self):
        """`ai doctor` is where an operator has asked to be told, so this is the
        one place the diagnosis must be complete rather than merely honest."""
        from conftest import run_cli

        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=False),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", return_value=_unrepairable()),
            patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        ):
            _, stdout, _ = run_cli(["ai", "doctor"])

        assert "MISS" in stdout
        assert MISSING_LIB in stdout

    def test_given_a_healthy_tmux_when_doctor_runs_then_no_repair_is_attempted(self):
        from conftest import run_cli

        with (
            patch("ai_cli.tmux_setup.tmux_present", return_value=True),
            patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
            patch("ai_cli.tmux_setup.repair_tmux_loader_path", side_effect=AssertionError("repaired a healthy tmux")),
            patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        ):
            _, stdout, _ = run_cli(["ai", "doctor"])

        assert "OK" in stdout


def test_the_read_only_tmux_probe_argv_is_still_permitted_by_the_suite_guard():
    """The repair spawns ``tmux -V``. If the suite's guard ever tightened to
    reject it, every test above would pass while the real path could not run."""
    from conftest import _reject_real_agent_process

    _reject_real_agent_process(["tmux", "-V"])
    with pytest.raises(RuntimeError):
        _reject_real_agent_process(["tmux", "new-session", "-d", "-s", "x"])


def test_the_version_probe_still_reports_a_broken_binary_as_unrunnable():
    """``tmux_runs`` is the predicate every decision above now hangs on, so its
    behaviour against a real non-zero exit is load-bearing."""
    with (
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["tmux", "-V"],
                returncode=127,
                stdout="",
                stderr=f"tmux: error while loading shared libraries: {MISSING_LIB}",
            ),
        ),
    ):
        assert tmux_setup.tmux_runs() is False
