"""``python -m ai_cli.worktree_move <checkout>...`` cleans a checkout moved before the fix.

A worktree moved by an older release still carries ``__pycache__`` bytecode naming its old
directory; this entry runs the same purge a move now runs, once per checkout given.
"""

from __future__ import annotations

import importlib.util
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _compiled(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "mod.py"
    source.write_text("x = 1\n", encoding="utf-8")
    return Path(py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)), doraise=True))


@pytest.fixture(autouse=True)
def _local_bytecode(monkeypatch):
    """Bytecode written beside its source, as the purge expects, whatever the runner's prefix."""
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setattr(sys, "pycache_prefix", None)


def _run(*paths: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPYCACHEPREFIX"}
    return subprocess.run(
        [sys.executable, "-m", "ai_cli.worktree_move", *map(str, paths)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_given_a_checkout_with_stale_caches_when_purged_by_hand_then_only_the_venv_cache_survives(tmp_path):
    checkout = tmp_path / "myproject"
    checkout.mkdir()
    _git("init", "-q", cwd=checkout)
    stale = _compiled(checkout / "pkg")
    kept = _compiled(checkout / ".venv" / "lib" / "dep")

    res = _run(checkout)

    assert res.returncode == 0, res.stderr
    assert not stale.parent.exists()
    assert kept.is_file()
    lines = res.stdout.splitlines()
    assert len(lines) == 1
    assert "[migrate] cleanup: " in lines[0]
    assert lines[0].endswith(f"purged stale bytecode caches in {checkout.resolve()}")


def test_given_a_path_that_is_not_a_checkout_when_purged_by_hand_then_it_is_refused_and_nothing_changes(tmp_path):
    checkout = tmp_path / "myproject"
    checkout.mkdir()
    _git("init", "-q", cwd=checkout)
    stale = _compiled(checkout / "pkg")
    plain = tmp_path / "notarepo"
    inside = _compiled(plain / "pkg")
    missing = tmp_path / "missing"

    for bad in (plain, missing, checkout / "pkg"):
        res = _run(checkout, bad)

        assert res.returncode != 0
        assert str(bad) in res.stderr
        assert res.stdout == ""
        assert stale.is_file(), "a refusal purges no checkout at all"
        assert inside.is_file()
