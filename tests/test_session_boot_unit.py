"""Boot unit management uses a fake service manager on every platform."""

import plistlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from ai_cli import main
from ai_cli import session_boot_unit as boot


@pytest.fixture(params=["linux", "darwin"])
def unit(tmp_path, monkeypatch, request):
    home = tmp_path / "home"
    executable = tmp_path / "my tools" / "ai"
    executable.parent.mkdir()
    executable.write_text("entry point", encoding="utf-8")
    monkeypatch.setattr(boot, "sys", SimpleNamespace(platform=request.param))
    monkeypatch.setattr(boot.Path, "home", lambda: home)
    monkeypatch.setattr(boot.shutil, "which", lambda name: str(executable))
    monkeypatch.setattr(boot.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("PATH", str(executable.parent))
    cfg = {"session_relaunch": {"on_boot": True}}
    monkeypatch.setattr(boot.state.config, "load_config", lambda: cfg)
    enabled = [False]
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if "is-enabled" in argv or "print" in argv:
            return subprocess.CompletedProcess(argv, 0 if enabled[0] else 1, "", "")
        if "enable" in argv or "bootstrap" in argv:
            enabled[0] = True
        if "disable" in argv or "bootout" in argv:
            enabled[0] = False
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(boot.subprocess, "run", runner)
    return SimpleNamespace(
        home=home,
        executable=executable,
        cfg=cfg,
        enabled=enabled,
        calls=calls,
        platform=request.param,
        invoke=lambda *args: CliRunner().invoke(main._cli_group, ["sessions", "boot-unit", *args]),
        files=lambda: boot.render(
            platform=request.param, home=home, path=str(executable.parent), executable=str(executable)
        ),
    )


def test_given_on_boot_true_when_installed_then_ac_3_1_unit_is_installed_and_enabled(unit):
    result = unit.invoke("install")
    assert result.exit_code == 0, result.output
    assert unit.enabled[0]
    assert {p: p.read_bytes() for p in unit.files()} == unit.files()
    assert "installed" in unit.invoke("status").output
    if unit.platform == "linux":
        assert ["systemctl", "--user", "enable", f"{boot.UNIT}.service"] in unit.calls
        assert b"WantedBy=default.target" in next(iter(unit.files().values()))
    else:
        assert ["launchctl", "enable", f"gui/1000/{boot.LABEL}"] in unit.calls
        assert any("bootstrap" in argv for argv in unit.calls)


@pytest.mark.parametrize("file_present", [True, False])
def test_given_on_boot_false_when_installed_then_ac_3_2_no_unit_remains_enabled(unit, file_present):
    assert unit.invoke("install").exit_code == 0
    if not file_present:
        for path, content in unit.files().items():
            assert path.read_bytes() == content
            path.unlink()
    unit.cfg["session_relaunch"]["on_boot"] = False
    result = unit.invoke("install")
    assert result.exit_code == 0, result.output
    assert not unit.enabled[0]
    assert not any(p.exists() for p in unit.files())
    assert "0 mutations" in unit.invoke("install").output


def test_given_unchanged_install_when_repeated_then_ac_b_1_zero_writes_or_manager_mutations(unit, monkeypatch):
    assert unit.invoke("install").exit_code == 0
    mtimes = {p: p.stat().st_mtime_ns for p in unit.files()}
    unit.calls.clear()
    monkeypatch.setattr(boot, "write_unit", lambda *args: pytest.fail("unexpected unit write"))
    result = unit.invoke("install")
    assert result.exit_code == 0, result.output
    assert "0 mutations" in result.output
    assert mtimes == {p: p.stat().st_mtime_ns for p in unit.files()}
    assert all("print" in argv or "is-enabled" in argv for argv in unit.calls)


@pytest.mark.parametrize("action", ["install", "uninstall"])
@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("on_boot", [False, True])
def test_given_dry_run_when_managed_then_ac_b_2_exact_plan_without_mutations(unit, action, installed, on_boot):
    if installed:
        assert unit.invoke("install").exit_code == 0
    unit.cfg["session_relaunch"]["on_boot"] = on_boot
    before = {p: p.read_bytes() for p in unit.files() if p.exists()}
    unit.calls.clear()
    result = unit.invoke(action, "--dry-run")
    assert result.exit_code == 0, result.output
    for path, content in unit.files().items():
        assert str(path) in result.output
        assert content.decode("utf-8") in result.output
    assert ("enable" if action == "install" and on_boot else "disable") in result.output
    assert "0 mutations" in result.output
    assert {p: p.read_bytes() for p in unit.files() if p.exists()} == before
    assert not unit.calls


def test_given_supported_platform_when_rendered_then_ac_b_3_absolute_entry_point_without_terminal(unit):
    content = next(iter(unit.files().values()))
    assert not any(name in content.lower() for name in (b"iterm", b"terminal", b"osascript", b"gnome", b"konsole"))
    if unit.platform == "darwin":
        doc = plistlib.loads(content)
        assert doc["ProgramArguments"] == [str(unit.executable), "sessions", "relaunch", "--boot"]
        assert doc["RunAtLoad"] is True
        assert doc["AbandonProcessGroup"] is True
        assert "KeepAlive" not in doc
    else:
        assert f'ExecStart="{unit.executable}" sessions relaunch --boot' in content.decode()
        assert b"Type=oneshot" in content and b"RemainAfterExit" not in content
        assert b"KillMode=process" in content and b"TimeoutStartSec=infinity" in content


def test_given_windows_when_managed_then_ac_b_4_refused_without_side_effects(unit, monkeypatch):
    monkeypatch.setattr(boot, "sys", SimpleNamespace(platform="win32"))
    result = unit.invoke("install")
    assert result.exit_code != 0
    assert "macOS and Linux" in result.output
    assert not unit.calls
    assert not any(p.exists() for p in unit.files())


def test_given_explicit_state_when_installing_then_caller_input_is_used_without_rewriting_config(unit):
    unit.cfg["session_relaunch"]["on_boot"] = False
    assert unit.invoke("install", "-b", "true").exit_code == 0
    assert unit.enabled[0]
    assert not unit.cfg["session_relaunch"]["on_boot"]
    assert unit.invoke("install", "--on-boot", "false").exit_code == 0
    assert not unit.enabled[0]


def test_given_installed_unit_when_uninstalled_then_reversible_without_changing_profile(unit):
    assert unit.invoke("install").exit_code == 0
    assert unit.invoke("uninstall").exit_code == 0
    assert not unit.enabled[0]
    assert not any(p.exists() for p in unit.files())
    assert unit.cfg["session_relaunch"]["on_boot"]
    assert "0 mutations" in unit.invoke("uninstall").output


def test_given_service_manager_failure_when_disabling_then_unit_is_retained_and_error_returned(unit, monkeypatch):
    assert unit.invoke("install").exit_code == 0
    original = boot.subprocess.run

    def refuse(argv, **kwargs):
        if "disable" in argv or "bootout" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "sensitive manager diagnostics")
        return original(argv, **kwargs)

    monkeypatch.setattr(boot.subprocess, "run", refuse)
    result = unit.invoke("uninstall")
    assert result.exit_code != 0
    assert "sensitive manager diagnostics" not in result.output
    assert all(p.exists() for p in unit.files())


def test_given_existing_unit_when_updated_then_replace_is_atomic_and_only_changed_bytes_are_written(unit, monkeypatch):
    assert unit.invoke("install").exit_code == 0
    target = next(iter(unit.files()))
    target.write_text("previous unit\n", encoding="utf-8")
    original = Path.replace
    replacements = []

    def replace(source, destination):
        assert Path(destination).read_bytes() == b"previous unit\n"
        assert source.read_bytes() == unit.files()[target]
        replacements.append((source, destination))
        return original(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    result = unit.invoke("install")
    assert result.exit_code == 0, result.output
    assert len(replacements) == 1
    assert target.read_bytes() == unit.files()[target]
    assert not replacements[0][0].exists()


def test_given_manager_unavailable_when_managed_then_failure_is_reported_without_file_changes(unit, monkeypatch):
    monkeypatch.setattr(
        boot.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 5, "", "private diagnostics")
    )
    result = unit.invoke("install")
    assert result.exit_code != 0
    assert "cannot determine boot-unit state" in result.output
    assert "private diagnostics" not in result.output
    assert not any(p.exists() for p in unit.files())


def test_given_xdg_overrides_when_rendered_then_service_uses_the_same_config_and_state(unit, monkeypatch):
    state_home = unit.home / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", "relative")
    content = next(iter(unit.files().values()))
    if unit.platform == "darwin":
        environment = plistlib.loads(content)["EnvironmentVariables"]
        assert environment["XDG_CONFIG_HOME"] == str(unit.home / "config")
        assert environment["XDG_STATE_HOME"] == str(state_home)
        assert "XDG_CACHE_HOME" not in environment
    else:
        assert f'"XDG_CONFIG_HOME={unit.home / "config"}"' in content.decode()
        assert f'"XDG_STATE_HOME={state_home}"' in content.decode()
        assert b"XDG_CACHE_HOME" not in content


def test_given_relative_entry_point_when_rendered_then_invalid_boot_unit_is_refused(unit):
    with pytest.raises(ValueError, match="absolute ai"):
        boot.render(platform=unit.platform, home=unit.home, path="", executable="ai")
