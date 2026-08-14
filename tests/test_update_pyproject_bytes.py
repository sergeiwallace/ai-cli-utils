"""Regression tests: ``ai update`` must not rewrite pyproject.toml's line endings.

``ai update`` bumps ``pyproject.toml``'s version to a unique ``.post<timestamp>``
so uv cannot serve a cached build, installs, then restores the original file. The
round trip went through ``Path.read_text()`` / ``Path.write_text()``, and
``read_text()`` applies universal-newline translation: a CRLF file arrived as LF
in memory, so the "restore" reverted the version but silently converted every
line ending. The result was a whole-file phantom diff that reappeared on every
update run and has already been mistaken for another session's uncommitted work.

Every assertion here reads the file in **binary** mode. A text-mode read
normalizes the exact difference under test, so the same assertions written with
``read_text()`` pass against the unfixed code — vacuously.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.main import _do_update_or_deploy, cli

REPO_ROOT = Path(__file__).resolve().parent.parent

CRLF_PYPROJECT = (
    b"[project]\r\n"
    b'name = "myproject"\r\n'
    b'version = "0.1.0"\r\n'
    b"\r\n"
    b"[tool.myproject]\r\n"
    b'note = "trailing content must survive the round trip"\r\n'
)

LF_PYPROJECT = CRLF_PYPROJECT.replace(b"\r\n", b"\n")


@pytest.fixture
def project(tmp_path):
    """A throwaway project tree whose pyproject.toml the test writes itself."""
    return tmp_path


def _install_recorder(pyproject: Path, seen: dict):
    """Fake ``subprocess.run`` that snapshots pyproject.toml at uv-install time."""

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "install" in cmd:
            seen["at_install"] = pyproject.read_bytes()
        return MagicMock(returncode=0, stdout="", stderr="")

    return fake_run


def _run_update(project_path: Path, fake_run, argv=("ai", "update")):
    with (
        patch("sys.argv", list(argv)),
        patch("ai_cli.config.load_config", return_value={"deploy": {"project_path": str(project_path)}}),
        patch("ai_cli.config.get_xdg_state_home", return_value=project_path / "state"),
        patch("subprocess.run", side_effect=fake_run),
    ):
        with pytest.raises(SystemExit) as exc:
            cli()
    return exc.value.code


class TestPyprojectByteRoundTrip:
    def test_given_a_crlf_pyproject_when_update_bumps_the_version_then_the_file_is_restored_byte_for_byte(
        self, project
    ):
        pyproject = project / "pyproject.toml"
        pyproject.write_bytes(CRLF_PYPROJECT)
        seen: dict = {}

        assert _run_update(project, _install_recorder(pyproject, seen)) == 0

        # Binary read: read_text() would normalize the very bytes under test.
        assert pyproject.read_bytes() == CRLF_PYPROJECT

    def test_given_a_crlf_pyproject_when_the_version_is_bumped_then_only_the_version_bytes_differ(self, project):
        pyproject = project / "pyproject.toml"
        pyproject.write_bytes(CRLF_PYPROJECT)
        seen: dict = {}

        assert _run_update(project, _install_recorder(pyproject, seen)) == 0

        bumped = seen["at_install"]
        m = re.search(rb'version = "(0\.1\.0\.post\d+)"\r\n', bumped)
        assert m, f"expected a CRLF-terminated .post version line, got {bumped!r}"
        # Substituting the bumped version back must reproduce the original exactly:
        # nothing else — line endings included — was allowed to change.
        assert bumped.replace(m.group(1), b"0.1.0") == CRLF_PYPROJECT

    def test_given_an_lf_pyproject_when_update_bumps_the_version_then_it_stays_lf(self, project):
        """Control: the fix must be ending-agnostic, not CRLF-specific."""
        pyproject = project / "pyproject.toml"
        pyproject.write_bytes(LF_PYPROJECT)
        seen: dict = {}

        assert _run_update(project, _install_recorder(pyproject, seen)) == 0

        assert pyproject.read_bytes() == LF_PYPROJECT
        assert b"\r\n" not in seen["at_install"]

    def test_given_a_crlf_pyproject_when_the_install_is_interrupted_then_the_original_bytes_are_restored(self, project):
        pyproject = project / "pyproject.toml"
        pyproject.write_bytes(CRLF_PYPROJECT)

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "install" in cmd:
                raise KeyboardInterrupt
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=project / "state"),
            patch("subprocess.run", side_effect=fake_run),
            pytest.raises(KeyboardInterrupt),
        ):
            _do_update_or_deploy(force_reinstall=False, config={"deploy": {"project_path": str(project)}})

        assert pyproject.read_bytes() == CRLF_PYPROJECT


class TestRepoLineEndingsAreCanonical:
    """AC-1: a committed ``.gitattributes`` makes the CRLF flip impossible."""

    def test_given_the_repo_when_gitattributes_is_read_then_text_files_are_pinned_to_lf(self):
        attributes = REPO_ROOT / ".gitattributes"
        assert attributes.is_file(), ".gitattributes is missing — nothing pins line endings"
        assert re.search(r"^\*\s+text=auto\s+eol=lf\s*$", attributes.read_text(), re.MULTILINE)

    def test_given_the_committed_pyproject_when_its_blob_is_decoded_then_it_has_no_crlf(self):
        """The blob, not the checkout: eol=lf can mask a CRLF blob in the worktree."""
        blob = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "HEAD:pyproject.toml"],
            capture_output=True,
            check=False,
        )
        if blob.returncode != 0:
            pytest.skip("not a git checkout")
        assert b"\r\n" not in blob.stdout
