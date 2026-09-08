"""Session-launch auto-update: install when the source changed, stay quiet otherwise.

``ai c <n>`` used to compare the repository's ``HEAD`` against a stamp and run a
full ``ai update --force`` on any difference, streaming git's and uv's whole
transcript into the terminal immediately before the session painted. A commit
pointer is the wrong question in both directions: it advances for commits that
change nothing that ships (a docs edit, a task-tracker sync) and does not move at
all for an uncommitted edit under ``src/``.

The trigger is now a content fingerprint of the packaged files. The guarantee that
motivated the unconditional reinstall is kept, and is what
``TestInstalledCodeIsTheNewCode`` exists to prove: the install is not editable, so
a source change needs a genuine reinstall, and ``ai update`` gives uv a unique
``.post<timestamp>`` version so it cannot serve a cached build. That test asserts
on the observable outcome — it loads the "installed" module and calls it — rather
than on the decision to reinstall.

No test here spawns a real ``uv``: the installer is emulated in-process by copying
the source tree, which is what ``uv tool install`` does to the code under test.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.main import (
    UPDATE_VERBOSE_ENV,
    _auto_update_if_stale,
    _do_update_or_deploy,
    _installed_source_fingerprint,
    cli,
)

PYPROJECT = '[project]\nname = "myproject"\nversion = "0.1.0"\n'


@pytest.fixture
def project(tmp_path):
    """A source tree shaped like the real one: pyproject.toml plus src/<pkg>/."""
    root = tmp_path / "myproject"
    (root / "src" / "ai_cli_demo").mkdir(parents=True)
    (root / "pyproject.toml").write_text(PYPROJECT)
    _source(root).write_text('def value():\n    return "old"\n')
    return root


@pytest.fixture
def state(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


def _source(root: Path) -> Path:
    return root / "src" / "ai_cli_demo" / "value.py"


def _stamp(state_dir: Path) -> Path:
    return state_dir / "last_install_fingerprint.txt"


def _record_installed(project_root: Path, state_dir: Path) -> None:
    """Mark the current source as the build that is already installed."""
    fingerprint = _installed_source_fingerprint(project_root)
    assert fingerprint is not None
    _stamp(state_dir).write_text(fingerprint)


def _loaded_value(installed_root: Path) -> str:
    """Load the installed copy and call it — the code that would actually run.

    Compiled from source text rather than imported: CPython's bytecode cache keys
    on (mtime-in-whole-seconds, size), and ``uv``'s install copies preserve mtime,
    so two same-length versions written inside one second alias to the same
    ``.pyc``. An import-based probe returned the *old* value after a correct
    install — a false failure, and in the other direction it would have been a
    false pass.
    """
    path = installed_root / "ai_cli_demo" / "value.py"
    namespace: dict = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    return namespace["value"]()


class _Launch:
    """Drive ``_auto_update_if_stale`` with git, uv and the ``ai`` re-exec faked.

    The one process boundary that is faked is ``ai update`` re-invoking the
    installed ``ai``: it is run in-process instead, with its stdout captured the
    way ``capture_output=True`` would capture it. Everything between that call and
    the "installed" tree is the real code path.
    """

    def __init__(self, project_root: Path, state_dir: Path, installed_root: Path | None = None):
        self.project = project_root
        self.state = state_dir
        self.installed = installed_root
        self.commands: list[list[str]] = []
        self.install_versions: list[str] = []
        self.uv_kwargs: list[dict] = []
        self.update_kwargs: list[dict] = []

    # -- the fake uv: copy the source tree, as a real tool install would --------
    def seed_installed(self):
        """Copy the current source into the installed tree without a launch."""
        self._install({})

    def _install(self, kwargs):
        self.install_versions.append((self.project / "pyproject.toml").read_text())
        self.uv_kwargs.append(kwargs)
        if self.installed is not None:
            src_root = self.project / "src"
            for path in src_root.rglob("*.py"):
                dst = self.installed / path.relative_to(src_root)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
        return MagicMock(returncode=0, stdout="Resolved 1 package\nInstalled 1 package\n", stderr="")

    def _child_update(self, cmd, kwargs):
        """Run `ai update --force [--quiet]` in-process, capturing its stdout."""
        self.update_kwargs.append(kwargs)
        quiet = "--quiet" in cmd
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                _do_update_or_deploy(
                    force_reinstall="--force" in cmd,
                    config={"deploy": {"project_path": str(self.project)}},
                    quiet=quiet,
                )
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
        captured = kwargs.get("capture_output", False)
        return MagicMock(
            returncode=code,
            stdout=out.getvalue() if captured else "",
            stderr=err.getvalue() if captured else "",
        )

    def run(self, cmd, **kwargs):
        cmd = list(cmd)
        self.commands.append(cmd)
        if len(cmd) >= 2 and cmd[1] == "update":
            return self._child_update(cmd, kwargs)
        if "install" in cmd:
            return self._install(kwargs)
        return MagicMock(returncode=0, stdout="", stderr="")

    @property
    def installs(self) -> list[list[str]]:
        return [c for c in self.commands if "install" in c]

    @property
    def updates(self) -> list[list[str]]:
        return [c for c in self.commands if len(c) >= 2 and c[1] == "update"]

    def launch(self):
        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=self.project),
            patch("ai_cli.config.get_xdg_state_home", return_value=self.state),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("subprocess.run", side_effect=self.run),
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(self.project)}})


class TestNoWorkWhenNothingChanged:
    def test_given_the_installed_build_matches_the_source_when_a_session_launches_then_nothing_runs(
        self, project, state
    ):
        _record_installed(project, state)
        before = (project / "pyproject.toml").read_bytes()
        launch = _Launch(project, state)

        launch.launch()

        # No pull, no version bump, no uv tool install — no subprocess at all.
        assert launch.commands == []
        assert (project / "pyproject.toml").read_bytes() == before

    def test_given_a_commit_that_touches_no_packaged_file_when_a_session_launches_then_nothing_is_reinstalled(
        self, project, state
    ):
        """AC-2: docs/ and task-tracker commits move HEAD but change nothing installed."""
        _record_installed(project, state)
        (project / "docs").mkdir()
        (project / "docs" / "notes.md").write_text("# notes\n")
        (project / ".beads").mkdir()
        (project / ".beads" / "issues.jsonl").write_text('{"id": "1"}\n')
        (project / "README.md").write_text("changed\n")
        launch = _Launch(project, state)

        launch.launch()

        assert launch.updates == []
        assert launch.installs == []


class TestInstalledCodeIsTheNewCode:
    """AC-6: the quieting must never let a changed source file go uninstalled."""

    def test_given_an_edited_source_file_when_a_session_launches_then_the_installed_code_is_the_new_code(
        self, project, state, tmp_path
    ):
        installed = tmp_path / "installed"
        installed.mkdir()
        # What is installed right now, and the stamp that says so.
        launch = _Launch(project, state, installed_root=installed)
        launch.seed_installed()
        _record_installed(project, state)
        assert _loaded_value(installed) == "old"

        # An uncommitted edit to a packaged file: HEAD does not move for this.
        _source(project).write_text('def value():\n    return "new"\n')

        launch.launch()

        # The observable outcome, not the decision: the code that would now run.
        assert _loaded_value(installed) == "new"
        # And it was rebuilt under a unique version, so uv could not serve its cache.
        bumped = [p for p in launch.install_versions if ".post" in p]
        assert bumped, f"no cache-defeating version was written: {launch.install_versions}"
        assert any("--reinstall" in cmd for cmd in launch.installs)

    def test_given_the_source_was_just_installed_when_a_second_launch_happens_then_it_does_not_reinstall(
        self, project, state, tmp_path
    ):
        """The stamp the install writes must actually match what the next launch computes."""
        installed = tmp_path / "installed"
        installed.mkdir()
        first = _Launch(project, state, installed_root=installed)
        first.launch()
        assert first.installs, "first launch should have installed an unstamped source"

        second = _Launch(project, state, installed_root=installed)
        second.launch()

        assert second.commands == []


class TestQuietOutput:
    def test_given_a_changed_source_when_the_launch_reinstalls_then_it_prints_one_line(
        self, project, state, tmp_path, capsys
    ):
        """AC-3: one concise line naming the version and the reason."""
        launch = _Launch(project, state, installed_root=tmp_path / "installed")

        launch.launch()

        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1, f"expected exactly one line, got {out}"
        assert out[0].startswith("ai-cli-utils 0.1.0.post")
        assert "installed (cache-bypassing reinstall)" in out[0]

    def test_given_a_quiet_launch_when_it_reinstalls_then_git_and_uv_output_is_captured(self, project, state):
        launch = _Launch(project, state, installed_root=None)

        launch.launch()

        assert launch.updates and "--quiet" in launch.updates[0]
        assert launch.update_kwargs[0].get("capture_output") is True
        # ...and the child captured uv rather than letting it reach the terminal.
        assert launch.uv_kwargs and launch.uv_kwargs[0].get("capture_output") is True

    def test_given_a_failing_reinstall_when_the_launch_is_quiet_then_the_transcript_is_surfaced(
        self, project, state, capsys
    ):
        """AC-4: suppressing the success path must not hide a failure."""
        launch = _Launch(project, state)

        def failing_run(cmd, **kwargs):
            cmd = list(cmd)
            launch.commands.append(cmd)
            if len(cmd) >= 2 and cmd[1] == "update":
                return MagicMock(returncode=1, stdout="uv: resolving\n", stderr="uv: no space left on device\n")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=project),
            patch("ai_cli.config.get_xdg_state_home", return_value=state),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("subprocess.run", side_effect=failing_run),
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(project)}})

        err = capsys.readouterr().err
        assert "auto-update failed" in err
        assert "uv: resolving" in err
        assert "no space left on device" in err
        # A failed install must not be remembered as done, or the stale build sticks.
        assert not _stamp(state).exists()


class TestEscapeHatches:
    def test_given_the_verbose_env_var_when_a_session_launches_then_the_full_transcript_is_shown(
        self, project, state, tmp_path
    ):
        """AC-5: the operator can always watch the whole update happen."""
        launch = _Launch(project, state, installed_root=tmp_path / "installed")

        with patch.dict(os.environ, {UPDATE_VERBOSE_ENV: "1"}):
            launch.launch()

        assert launch.updates and "--quiet" not in launch.updates[0]
        assert launch.update_kwargs[0].get("capture_output") is False
        assert launch.uv_kwargs[0].get("capture_output") is False

    def test_given_verbose_and_quiet_together_when_update_runs_then_verbose_wins(self, project, capsys):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("sys.argv", ["ai", "update", "--quiet", "--verbose"]),
            patch("ai_cli.config.load_config", return_value={"deploy": {"project_path": str(project)}}),
            patch("ai_cli.config.get_xdg_state_home", return_value=project / "state"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        assert "Pulling latest from origin..." in capsys.readouterr().out

    def test_given_a_matching_fingerprint_when_update_force_is_run_by_hand_then_it_still_reinstalls(
        self, project, state, tmp_path
    ):
        """AC-5: the on-demand cache refresh never consults the stamp."""
        _record_installed(project, state)
        launch = _Launch(project, state, installed_root=tmp_path / "installed")

        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=state),
            patch("subprocess.run", side_effect=launch.run),
        ):
            with pytest.raises(SystemExit) as exc:
                _do_update_or_deploy(
                    force_reinstall=True, config={"deploy": {"project_path": str(project)}}, quiet=False
                )

        assert exc.value.code == 0
        assert any("--reinstall" in cmd for cmd in launch.installs)


class TestFingerprintScope:
    def test_given_a_docs_file_when_the_fingerprint_is_computed_then_it_does_not_change(self, project):
        before = _installed_source_fingerprint(project)
        (project / "docs").mkdir()
        (project / "docs" / "guide.md").write_text("prose\n")

        assert _installed_source_fingerprint(project) == before

    def test_given_an_edited_packaged_file_when_the_fingerprint_is_computed_then_it_changes(self, project):
        before = _installed_source_fingerprint(project)
        _source(project).write_text('def value():\n    return "new"\n')

        assert _installed_source_fingerprint(project) != before

    def test_given_a_bytecode_cache_when_the_fingerprint_is_computed_then_it_is_ignored(self, project):
        before = _installed_source_fingerprint(project)
        cache = project / "src" / "ai_cli_demo" / "__pycache__"
        cache.mkdir()
        (cache / "value.cpython-313.pyc").write_bytes(b"\x00\x01")

        assert _installed_source_fingerprint(project) == before

    def test_given_an_unreadable_project_when_the_fingerprint_is_computed_then_it_is_none(self, tmp_path):
        assert _installed_source_fingerprint(tmp_path / "nope") is None
