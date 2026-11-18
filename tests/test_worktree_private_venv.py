"""A new session worktree gets its own virtual environment, not a link to the main checkout's.

``_initialize_worktree`` linked ``.venv`` to the main checkout, so every session of a project
shared one environment: a ``uv sync`` in one session rewrote the editable install every other
session imported, pointing it at that session's checkout. A uv-locked project's worktree now
gets a private ``.venv`` from ``uv sync --frozen`` run inside it before the worktree is
returned. ``[worktree] venv = "link"`` keeps the old shared link for hosts that want it.

These tests drive the real ``create_worktree`` against real repositories, with a stand-in
``uv`` on PATH that creates the environment and records how it was called.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ai_cli.config as config
from ai_cli.session import create_worktree

FAKE_UV = """\
import json, os, sys
from pathlib import Path
log = Path(os.environ["FAKE_UV_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                             "venv": os.environ.get("VIRTUAL_ENV"),
                             "project": os.environ.get("UV_PROJECT"),
                             "workdir": os.environ.get("UV_WORKING_DIR")}) + "\\n")
if os.environ.get("FAKE_UV_FAIL"):
    sys.stderr.write("error: failed to resolve the project\\n")
    sys.exit(2)
venv = Path(os.getcwd()) / ".venv"
venv.mkdir()
if os.environ.get("FAKE_UV_FAIL_AFTER_CREATE"):
    sys.stderr.write("error: failed to install a package\\n")
    sys.exit(1)
(venv / "pyvenv.cfg").write_text("home = /usr/bin\\n", encoding="utf-8")
"""


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A uv-locked project cloned from a bare remote, with the main checkout's own .venv."""
    remote = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "pyproject.toml").write_text(
        '[project]\nname = "myproject"\nversion = "0.1.0"\n\n[tool.ai-cli]\ntask_prefix = "myproject"\n',
        encoding="utf-8",
    )
    (seed / "uv.lock").write_text("version = 1\n")
    (seed / ".gitignore").write_text(".venv/\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)
    checkout = tmp_path / "myproject"
    _git("clone", "-q", str(remote), str(checkout), cwd=tmp_path)
    (checkout / ".venv").mkdir()
    (checkout / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (checkout / ".claude").mkdir()
    return checkout


@pytest.fixture
def fake_uv(tmp_path, monkeypatch):
    """A ``uv`` on PATH that makes ``.venv`` in its cwd; returns its call log."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fake_uv.py"
    script.write_text(FAKE_UV, encoding="utf-8")
    if sys.platform == "win32":
        (bindir / "uv.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        uv = bindir / "uv"
        uv.write_text(f"#!{sys.executable}\nexec(open({str(script)!r}).read())\n", encoding="utf-8")
        uv.chmod(0o755)
    log = tmp_path / "uv-calls.jsonl"
    monkeypatch.setenv("FAKE_UV_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "myproject" / ".venv"))
    monkeypatch.setenv("UV_PROJECT", str(tmp_path / "myproject"))
    monkeypatch.setenv("UV_WORKING_DIR", str(tmp_path / "myproject"))
    return log


def _calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _write_config(text: str) -> None:
    path = config._config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_given_a_uv_locked_project_when_a_session_worktree_is_created_then_it_gets_its_own_venv(repo, fake_uv):
    slot = create_worktree("myproject-tst-1", repo_root=repo)

    assert slot is not None
    venv = slot / ".venv"
    assert venv.is_dir()
    assert not venv.is_symlink()
    assert venv.resolve() != (repo / ".venv").resolve()
    calls = _calls(fake_uv)
    assert [call["argv"] for call in calls] == [["sync", "--frozen"]]
    assert Path(calls[0]["cwd"]).resolve() == slot.resolve()
    # An activated environment, or a project or directory uv was pointed at, would otherwise
    # decide where uv installs.
    assert calls[0]["venv"] is None
    assert calls[0]["project"] is None
    assert calls[0]["workdir"] is None


def test_given_two_session_worktrees_when_one_environment_changes_then_the_other_and_main_do_not(repo, fake_uv):
    first = create_worktree("myproject-tst-1", repo_root=repo)
    second = create_worktree("myproject-tst-2", repo_root=repo)
    assert first is not None and second is not None

    (first / ".venv" / "marker.txt").write_text("written in session-1\n")

    assert not (second / ".venv" / "marker.txt").exists()
    assert not (repo / ".venv" / "marker.txt").exists()


def test_given_the_shared_links_when_a_session_worktree_is_created_then_they_still_point_at_main(repo, fake_uv):
    slot = create_worktree("myproject-tst-1", repo_root=repo)

    assert slot is not None
    assert (slot / ".claude").resolve() == (repo / ".claude").resolve()


def test_given_uv_sync_fails_when_a_session_worktree_is_created_then_the_launch_says_why(repo, fake_uv, monkeypatch):
    monkeypatch.setenv("FAKE_UV_FAIL", "1")

    with pytest.raises(RuntimeError) as raised:
        create_worktree("myproject-tst-1", repo_root=repo)

    message = str(raised.value)
    assert "uv sync --frozen" in message
    assert "failed to resolve the project" in message
    assert "[worktree] venv" in message


def test_given_no_uv_on_path_when_a_session_worktree_is_created_then_the_launch_says_how_to_continue(repo, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join(_git_dirs()))

    with pytest.raises(RuntimeError) as raised:
        create_worktree("myproject-tst-1", repo_root=repo)

    assert "`uv` is not on PATH" in str(raised.value)
    assert '[worktree] venv = "link"' in str(raised.value)


def _git_dirs() -> list[str]:
    """PATH entries that hold git but no uv, so the launch can still run git."""
    dirs = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and (Path(entry) / ("git.exe" if sys.platform == "win32" else "git")).exists():
            if not any((Path(entry) / name).exists() for name in ("uv", "uv.exe", "uv.cmd")):
                dirs.append(entry)
    return dirs


def test_given_venv_link_configured_when_a_session_worktree_is_created_then_it_shares_main(repo, fake_uv):
    _write_config('[worktree]\nvenv = "link"\n')

    slot = create_worktree("myproject-tst-1", repo_root=repo)

    assert slot is not None
    assert (slot / ".venv").resolve() == (repo / ".venv").resolve()
    assert _calls(fake_uv) == []


def test_given_a_reused_worktree_with_its_own_venv_when_relaunched_then_it_is_left_alone(repo, fake_uv):
    slot = create_worktree("myproject-tst-1", repo_root=repo)
    assert slot is not None
    (slot / ".venv" / "kept.txt").write_text("installed by the session\n")

    again = create_worktree("myproject-tst-1", repo_root=repo)

    assert again == slot
    assert (slot / ".venv" / "kept.txt").exists()
    assert len(_calls(fake_uv)) == 1


def test_given_a_sync_that_failed_after_creating_the_venv_when_relaunched_then_it_is_rebuilt(
    repo, fake_uv, monkeypatch
):
    monkeypatch.setenv("FAKE_UV_FAIL_AFTER_CREATE", "1")
    with pytest.raises(RuntimeError):
        create_worktree("myproject-tst-1", repo_root=repo)
    monkeypatch.delenv("FAKE_UV_FAIL_AFTER_CREATE")

    slot = create_worktree("myproject-tst-1", repo_root=repo)

    assert slot is not None
    assert (slot / ".venv" / "pyvenv.cfg").is_file()
    assert len(_calls(fake_uv)) == 2  # the incomplete environment is not taken as finished


@pytest.mark.parametrize("value", ["inf", "nan", "-5", "0", "true", '"10"'])
def test_given_an_unusable_sync_timeout_when_read_then_the_default_bounds_the_sync(value):
    _write_config(f"[worktree]\nvenv_sync_timeout_seconds = {value}\n")

    assert config.get_worktree_venv_sync_timeout() == config.DEFAULT_WORKTREE_VENV_SYNC_TIMEOUT
