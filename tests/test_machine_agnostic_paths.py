"""Facts about the machine must be resolved at runtime, not baked in at authoring time.

Every test here runs under a DIFFERENT home directory, a different machine identity and a
different projects root than the machine running the suite, because that is the only way to
tell a value that was resolved from one that happened to be right here. A hardcoded
``~/projects`` and a configured ``projects_dir`` that happens to be ``~/projects`` are
indistinguishable on the author's machine and diverge on everybody else's (AI-CLI-987p is the
one sibling case deliberately left for a configuration decision).
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

import ai_cli.config as config
import ai_cli.copier_update as copier_update
import ai_cli.sync as sync
import ai_cli.telemetry as telemetry
import ai_cli.trust as trust
from ai_cli.main import cmd_trust_backfill
from ai_cli.setup import _is_managed_platform

#: Captured at import, which is before any fixture has run. ``conftest`` installs an autouse
#: fixture that replaces this resolver with a constant naming the checkout's parent, so that
#: launch tests resolve a project wherever the repository is cloned. These tests are the ones
#: that exercise resolution itself, so they put the real implementation back and let the
#: config.toml they write be the only thing that answers -- which is also what makes them a
#: test of the call sites rather than of the fixture.
_REAL_GET_PROJECTS_DIR = config._get_projects_dir


def _foreign_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_body: str = "") -> Path:
    """Point this process at a home directory and config.toml that are not this machine's.

    ``HOME``/``USERPROFILE`` rather than patching ``Path.home``: the code under test reads the
    projects root through ``load_config()``, which resolves its own config directory from the
    environment, so a patched ``Path.home`` with the real ``XDG_CONFIG_HOME`` still reads the
    operator's live configuration and the test's answer becomes whatever that file says.
    """
    monkeypatch.setattr(config, "_get_projects_dir", _REAL_GET_PROJECTS_DIR)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("APPDATA", str(xdg))
    config_dir = xdg / "ai-cli-utils"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(config_body, encoding="utf-8")
    return home


def _projects_dir_config(projects_dir: Path) -> str:
    return f"[project]\nprojects_dir = {json.dumps(str(projects_dir))}\n"


# ---------------------------------------------------------------------------
# host identity
# ---------------------------------------------------------------------------


class TestMachineIdentity:
    def test_given_no_ai_host_and_a_marker_file_when_detecting_the_profile_then_the_marker_answers(
        self, tmp_path, monkeypatch
    ):
        marker = tmp_path / "machine"
        marker.write_text("sem-kg-ec2\n", encoding="utf-8")
        monkeypatch.setattr(config, "MACHINE_MARKER_FILE", marker, raising=False)
        monkeypatch.delenv("AI_HOST", raising=False)
        monkeypatch.setattr(socket, "gethostname", lambda: "ip-100-120-40-17")

        profile = config.detect_machine_profile()

        assert profile["host_id"] == "sem-kg-ec2"
        assert profile["host_id_source"] == str(marker)

    def test_given_ai_host_set_when_detecting_the_profile_then_the_environment_beats_the_marker(
        self, tmp_path, monkeypatch
    ):
        marker = tmp_path / "machine"
        marker.write_text("sem-kg-ec2\n", encoding="utf-8")
        monkeypatch.setattr(config, "MACHINE_MARKER_FILE", marker, raising=False)
        monkeypatch.setenv("AI_HOST", "acn-windows")

        profile = config.detect_machine_profile()

        assert profile["host_id"] == "acn-windows"
        assert profile["host_id_source"] == "AI_HOST"

    def test_given_an_empty_marker_file_when_detecting_the_profile_then_the_hostname_answers(
        self, tmp_path, monkeypatch
    ):
        marker = tmp_path / "machine"
        marker.write_text("\n", encoding="utf-8")
        monkeypatch.setattr(config, "MACHINE_MARKER_FILE", marker, raising=False)
        monkeypatch.delenv("AI_HOST", raising=False)
        monkeypatch.setattr(socket, "gethostname", lambda: "ip-100-120-40-17")

        profile = config.detect_machine_profile()

        assert profile["host_id"] == "ip-100-120-40-17"
        assert profile["host_id_source"] == config.HOST_ID_SOURCE_HOSTNAME

    def test_given_no_marker_when_registering_the_profile_then_the_hostname_fallback_is_reported(
        self, tmp_path, monkeypatch, capsys
    ):
        """The hostname tier is where a lease name becomes this machine's durable identity.

        ``host_id`` keys the per-machine config profile and the chief-of-staff registration, and
        is written once and never revisited. Standing in the hostname for a missing marker is a
        guess, so the registration says which tier answered rather than reporting the guess as a
        detection.
        """
        absent_marker = tmp_path / "no-such-marker"
        monkeypatch.setattr(config, "MACHINE_MARKER_FILE", absent_marker, raising=False)
        monkeypatch.delenv("AI_HOST", raising=False)
        monkeypatch.setattr(socket, "gethostname", lambda: "ip-100-120-40-17")
        config_file = tmp_path / "config.toml"
        config_file.write_text("[behavior]\nnotify_on_exit = true\n", encoding="utf-8")

        assert config.ensure_machine_profile_registered(config_file, {}) is True

        err = capsys.readouterr().err
        assert "ip-100-120-40-17" in err
        assert config.HOST_ID_SOURCE_HOSTNAME in err
        assert str(absent_marker) in err

    def test_given_a_marker_when_registering_the_profile_then_no_fallback_is_reported(
        self, tmp_path, monkeypatch, capsys
    ):
        marker = tmp_path / "machine"
        marker.write_text("sem-kg-ec2\n", encoding="utf-8")
        monkeypatch.setattr(config, "MACHINE_MARKER_FILE", marker, raising=False)
        monkeypatch.delenv("AI_HOST", raising=False)
        monkeypatch.setattr(socket, "gethostname", lambda: "ip-100-120-40-17")
        config_file = tmp_path / "config.toml"
        config_file.write_text("[behavior]\nnotify_on_exit = true\n", encoding="utf-8")

        assert config.ensure_machine_profile_registered(config_file, {}) is True

        err = capsys.readouterr().err
        assert "sem-kg-ec2" in err
        assert "ip-100-120-40-17" not in err
        assert 'host_id = "sem-kg-ec2"' in config_file.read_text(encoding="utf-8")

    def test_given_a_marker_when_telemetry_identifies_the_machine_then_it_uses_the_resolved_host_id(
        self, tmp_path, monkeypatch
    ):
        """Telemetry rows outlive the lease whose DNS name the hostname is."""
        marker = tmp_path / "machine"
        marker.write_text("sem-kg-ec2\n", encoding="utf-8")
        monkeypatch.setattr(config, "MACHINE_MARKER_FILE", marker, raising=False)
        monkeypatch.delenv("AI_HOST", raising=False)
        monkeypatch.setattr(socket, "gethostname", lambda: "ip-100-120-40-17")

        assert telemetry._get_machine_id() == "sem-kg-ec2"


# ---------------------------------------------------------------------------
# projects root
# ---------------------------------------------------------------------------


class TestConfiguredProjectsRoot:
    def test_given_a_configured_projects_dir_when_detecting_the_managed_platform_then_it_is_found(
        self, tmp_path, monkeypatch
    ):
        """``~/projects`` is a near-empty trap on at least one managed host.

        There the real root is elsewhere and ``[project] projects_dir`` names it. Reading the
        literal path makes ``ai setup`` decide there is no managed platform, and that decision
        is destructive: it copies CLAUDE-full.md over CLAUDE.md and marks the result
        assume-unchanged, so the swap is invisible in git status afterwards.
        """
        elsewhere = tmp_path / "real-projects"
        elsewhere.mkdir()
        (elsewhere / "CLAUDE.md").write_text("# shared config\n", encoding="utf-8")
        home = _foreign_home(tmp_path, monkeypatch, _projects_dir_config(elsewhere))
        assert not (home / "projects").exists()

        assert _is_managed_platform() is True

    def test_given_a_configured_projects_dir_without_the_shared_config_then_the_home_default_is_not_consulted(
        self, tmp_path, monkeypatch
    ):
        """The complementary arm: the configured root must be the only root consulted.

        Without it, a predicate that happened to check both roots would satisfy the test above
        while still answering from ``~/projects``.
        """
        elsewhere = tmp_path / "real-projects"
        elsewhere.mkdir()
        home = _foreign_home(tmp_path, monkeypatch, _projects_dir_config(elsewhere))
        (home / "projects").mkdir()
        (home / "projects" / "CLAUDE.md").write_text("# decoy\n", encoding="utf-8")

        assert _is_managed_platform() is False

    def test_given_a_configured_projects_dir_when_copier_update_runs_then_it_scans_that_root(
        self, tmp_path, monkeypatch
    ):
        elsewhere = tmp_path / "real-projects"
        elsewhere.mkdir()
        home = _foreign_home(tmp_path, monkeypatch, _projects_dir_config(elsewhere))
        assert not (home / "projects").exists()

        scanned: list[Path] = []

        def _record(projects_dir: Path) -> list[Path]:
            scanned.append(projects_dir)
            return []

        real_which = shutil.which
        monkeypatch.setattr(copier_update, "_find_copier_projects", _record)
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name, *a, **kw: "/usr/bin/copier" if name == "copier" else real_which(name, *a, **kw),
        )

        assert copier_update.run_copier_update() == 0
        assert scanned == [elsewhere]

    def test_given_a_configured_projects_dir_when_trust_backfill_runs_without_root_then_it_scans_that_root(
        self, tmp_path, monkeypatch
    ):
        elsewhere = tmp_path / "real-projects"
        elsewhere.mkdir()
        _foreign_home(tmp_path, monkeypatch, _projects_dir_config(elsewhere))

        scanned: list[Path] = []

        def _record(root) -> list[str]:
            scanned.append(Path(root))
            return []

        monkeypatch.setattr(trust, "backfill_projects_trust", _record)

        result = CliRunner().invoke(cmd_trust_backfill, [])

        assert result.exit_code == 0, result.output
        assert scanned == [elsewhere]

    def test_given_an_explicit_root_when_trust_backfill_runs_then_the_option_still_wins(self, tmp_path, monkeypatch):
        elsewhere = tmp_path / "real-projects"
        elsewhere.mkdir()
        explicit = tmp_path / "somewhere-else"
        explicit.mkdir()
        _foreign_home(tmp_path, monkeypatch, _projects_dir_config(elsewhere))

        scanned: list[Path] = []
        monkeypatch.setattr(trust, "backfill_projects_trust", lambda root: scanned.append(Path(root)) or [])

        result = CliRunner().invoke(cmd_trust_backfill, ["--root", str(explicit)])

        assert result.exit_code == 0, result.output
        assert scanned == [explicit]

    def test_given_a_configured_projects_dir_when_a_memories_only_pull_runs_then_it_uses_that_root(
        self, tmp_path, monkeypatch
    ):
        """The memories-only branch of a pull picked the literal root while its sibling did not.

        Both branches hand the root to ``apply_pull_files``, which rewrites transcript paths to
        point at this machine, so the two branches disagreeing means a memories-only pull repaths
        against a directory the operator does not use.
        """
        elsewhere = tmp_path / "real-projects"
        elsewhere.mkdir()
        _foreign_home(tmp_path, monkeypatch, _projects_dir_config(elsewhere))
        staging = tmp_path / "staging"
        staging.mkdir()

        captured: dict[str, object] = {}

        def _record(**kwargs):
            captured.update(kwargs)
            return {"applied_count": 0, "conflicts": []}

        monkeypatch.setattr(sync, "apply_pull_files", _record)
        cfg = sync.SyncConfig(
            staging_dir=staging,
            remote_url="",
            local_prefix="-home-user-projects-",
            remote_host="",
            source_machine="server",
        )

        assert sync._sync_pull(["--memories-only", "--dry-run", "--force"], cfg) == 0
        assert captured["local_projects_root"] == elsewhere
