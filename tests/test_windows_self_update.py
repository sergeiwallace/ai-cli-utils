"""Regression tests: self-update must not destroy the environment it runs from.

Defect: ``ai update --force`` ran ``uv tool install <project> --force --reinstall``
against the very uv tool environment whose ``Scripts/python.exe`` was the running
interpreter. Windows refuses to delete a directory holding a mapped executable
image, so uv removed ``Lib/site-packages`` and then failed on ``Scripts``, leaving
the tool environment with no packages at all -- while the caller printed
"auto-update failed, continuing with current version".
"""

import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.main import _auto_update_if_stale, cli

BIN_DIR = "Scripts" if sys.platform == "win32" else "bin"
PY_EXE = "python.exe" if sys.platform == "win32" else "python"


def _fake_uv_tool_venv(root: Path) -> Path:
    """Build a directory shaped like a uv tool environment.

    ``uv-receipt.toml`` at the environment root is uv's own marker for a tool
    environment, so it is what identifies one without a ``uv tool dir`` round trip.
    """
    venv = root / "toolvenv"
    (venv / BIN_DIR).mkdir(parents=True)
    (venv / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "ai-cli-utils" }]\n',
        encoding="utf-8",
    )
    (venv / BIN_DIR / PY_EXE).write_bytes(b"")
    return venv


class TestSelfUpdateDoesNotRecreateItsOwnEnvironment:
    """The causal contract: never issue a command that recreates the live env."""

    def test_when_running_from_uv_tool_venv_then_update_does_not_recreate_it(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
        venv = _fake_uv_tool_venv(tmp_path)

        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, (list, tuple)):
                calls.append([str(c) for c in cmd])
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("sys.argv", ["ai", "deploy", "--force"]),
            patch("ai_cli.config.load_config", return_value={"deploy": {"project_path": str(project)}}),
            patch.object(sys, "prefix", str(venv)),
            patch.object(sys, "base_prefix", str(tmp_path / "system-python")),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 0

        installs = [c for c in calls if "install" in c]
        assert installs, f"no install command was issued; calls={calls}"

        # The destructive form: `uv tool install <project> --force` tears the tool
        # environment down and rebuilds it, which cannot succeed while this
        # interpreter's own image lives in <venv>/Scripts.
        recreating = [c for c in installs if "tool" in c and "install" in c and "--force" in c]
        assert not recreating, f"update still recreates the live tool environment: {recreating}"

        # It must instead install into the environment that is already there.
        targeted = [c for c in installs if any(str(venv) in part for part in c)]
        assert targeted, f"no install targeted the running environment {venv}; installs={installs}"

    def test_when_not_running_from_a_tool_venv_then_normal_tool_install_is_kept(self, tmp_path):
        """Negative constraint: the ordinary path must not change.

        Without this, "never run `uv tool install`" would pass the test above while
        breaking every non-self-update install.
        """
        project = tmp_path / "proj"
        project.mkdir()
        (project / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')

        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, (list, tuple)):
                calls.append([str(c) for c in cmd])
            return MagicMock(returncode=0, stdout="", stderr="")

        # sys.prefix == sys.base_prefix -> not in a venv at all.
        with (
            patch("sys.argv", ["ai", "deploy", "--force"]),
            patch("ai_cli.config.load_config", return_value={"deploy": {"project_path": str(project)}}),
            patch.object(sys, "prefix", str(tmp_path / "sysroot")),
            patch.object(sys, "base_prefix", str(tmp_path / "sysroot")),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 0

        assert any("tool" in c and "install" in c for c in calls), (
            f"the ordinary `uv tool install` path regressed; calls={calls}"
        )


class TestFailedAutoUpdateReportsTheTruth:
    """A failed update must never claim the installation is usable."""

    def test_when_update_fails_and_environment_is_broken_then_says_so(self, tmp_path, capsys):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
        venv = _fake_uv_tool_venv(tmp_path)
        # A broken environment: uv removed site-packages, so a fresh interpreter
        # cannot import the package. Model it by making the probe fail.

        def fake_run(cmd, **kwargs):
            parts = [str(c) for c in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
            if "rev-parse" in parts:
                return MagicMock(returncode=0, stdout="new_hash\n", stderr="")
            if "update" in parts:
                return MagicMock(returncode=1, stdout="", stderr="")  # the failing upgrade
            if "-c" in parts:  # the import probe against the tool interpreter
                return MagicMock(returncode=1, stdout="", stderr="ModuleNotFoundError")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch.object(sys, "prefix", str(venv)),
            patch.object(sys, "base_prefix", str(tmp_path / "system-python")),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert updated is False
        err = capsys.readouterr().err
        # The defect: it reassured the user the install was still usable.
        assert "continuing with current version" not in err, f"a broken installation was reported as usable: {err!r}"
        # It must say the installation is broken and how to recover.
        assert "broken" in err.lower(), f"breakage not reported: {err!r}"


@pytest.mark.skipif(sys.platform != "win32", reason="mandatory image locking is Windows-specific")
class TestPlatformPremise:
    """Premise guard, not the regression test.

    Documents the OS behaviour the fix exists for. If Windows ever stopped locking
    a mapped image, this would go red and the fix's rationale would need revisiting.
    """

    def test_when_interpreter_image_is_live_then_its_directory_cannot_be_removed(self, tmp_path):
        venv = tmp_path / "realvenv"
        subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True)
        py = venv / BIN_DIR / PY_EXE
        assert py.exists()

        holder = subprocess.Popen(
            [str(py), "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            subprocess.run([str(py), "-c", "pass"], check=True, capture_output=True, timeout=60)
            with pytest.raises(PermissionError) as exc:
                shutil.rmtree(venv / BIN_DIR)
            assert getattr(exc.value, "winerror", None) == 5
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=15)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait(timeout=15)
