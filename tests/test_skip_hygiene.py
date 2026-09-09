"""A capability probe must run BEFORE the setup it decides the fate of.

THE BUG (AI-CLI-bug-tests-skip-capability-probe-bfqy). Several tests and fixtures
here decide at run time whether the host can support them — is zsh installed, is
this filesystem case-insensitive, can an isolated tmux server start — and they
were making that decision *after* doing the expensive part. Two costs, one of
them real:

* **Wasted work.** A worktree checkout on a throttled filesystem is seconds, and
  it is thrown away by a skip a few lines later.
* **A leak.** ``real_tmux_socket`` called ``mkdtemp`` and started a probe tmux
  server, and only *then* decided whether to skip — with the ``try``/``finally``
  that cleans both up starting *below* the skip. So on the skip path the temp
  directory was never removed and the probe server was never killed. That path
  was live on any host whose tmux could not start, which is exactly the host the
  skip exists for.

The distinction that matters, and why this is not "hoist every probe": a probe
whose own *input* is the setup cannot be hoisted. ``test_session_reload_mtime``
writes one file so it can ``stat`` it — the write IS the probe. That site is
correct and the meta-guard below allowlists it explicitly rather than silently.
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess

import pytest

TESTS_DIR = pathlib.Path(__file__).parent


# ---------------------------------------------------------------------------
# The leak
# ---------------------------------------------------------------------------


def test_given_the_isolated_tmux_probe_fails_when_it_skips_then_the_tempdir_is_removed(monkeypatch):
    """The real defect: a skip that runs before the cleanup contract exists.

    Drives the fixture's own generator with a probe forced to fail, so the skip
    path is the one under test, and asserts against the real filesystem that
    nothing was left behind.
    """
    from tests import test_stale_session_reaper as reaper

    created: list[pathlib.Path] = []
    real_mkdtemp = reaper.tempfile.mkdtemp

    def _recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(pathlib.Path(path))
        return path

    monkeypatch.setattr(reaper.tempfile, "mkdtemp", _recording_mkdtemp)
    monkeypatch.setattr(reaper.shutil, "which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    # Every tmux call fails, which is precisely what an unusable isolated server
    # looks like -- the condition the fixture's second skip exists for.
    monkeypatch.setattr(
        reaper,
        "_tmux_run",
        lambda *a, **k: subprocess.CompletedProcess(args=list(a), returncode=1, stdout="", stderr="no server"),
    )

    generator = reaper.isolated_tmux_socket()
    with pytest.raises(Exception, match="isolated tmux server unavailable") as caught:
        next(generator)
    assert caught.typename == "Skipped", f"expected a pytest skip, got {caught.typename}"

    assert created, "positive control: the fixture must actually have created a temp dir to leak"
    for path in created:
        assert not path.exists(), f"skip path leaked {path}"


def test_given_the_isolated_tmux_probe_succeeds_when_used_then_it_still_cleans_up_after(monkeypatch):
    """Anti-vacuity control. Without this, deleting the temp dir unconditionally
    at the top of the fixture would pass the case above and break every real user.
    """
    from tests import test_stale_session_reaper as reaper

    created: list[pathlib.Path] = []
    real_mkdtemp = reaper.tempfile.mkdtemp

    def _recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(pathlib.Path(path))
        return path

    monkeypatch.setattr(reaper.tempfile, "mkdtemp", _recording_mkdtemp)
    monkeypatch.setattr(reaper.shutil, "which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    monkeypatch.setattr(
        reaper,
        "_tmux_run",
        lambda *a, **k: subprocess.CompletedProcess(args=list(a), returncode=0, stdout="", stderr=""),
    )

    generator = reaper.isolated_tmux_socket()
    socket = next(generator)

    assert created and created[0].exists(), "the socket dir must exist while the fixture is in use"
    assert str(created[0]) in socket

    with pytest.raises(StopIteration):
        next(generator)
    assert not created[0].exists(), "the normal path must still clean up"


# ---------------------------------------------------------------------------
# The wasted setup
# ---------------------------------------------------------------------------


def test_given_a_case_sensitive_filesystem_when_probed_then_it_is_reported_case_sensitive(tmp_path):
    """The probe itself, against a real filesystem, both ways.

    ``filesystem_is_case_insensitive`` must answer from what the filesystem
    actually does, not from ``sys.platform`` — a case-insensitive volume can be
    mounted on Linux and a case-sensitive one on macOS, so a platform guess would
    be wrong in both directions.
    """
    from conftest import filesystem_is_case_insensitive

    answer = filesystem_is_case_insensitive(tmp_path)
    assert isinstance(answer, bool)

    # Cross-check against the same filesystem's own behaviour, established
    # independently of the helper.
    probe = tmp_path / "aicli-case-probe"
    probe.mkdir()
    assert answer == (tmp_path / "AICLI-CASE-PROBE").exists()


def test_given_the_probe_when_it_runs_then_it_leaves_nothing_behind(tmp_path):
    before = {p.name for p in tmp_path.iterdir()}

    from conftest import filesystem_is_case_insensitive

    filesystem_is_case_insensitive(tmp_path)

    assert {p.name for p in tmp_path.iterdir()} == before, "the probe must clean up after itself"


def test_given_a_case_sensitive_filesystem_when_the_fixture_is_requested_then_it_skips_without_setup(monkeypatch):
    """The point of hoisting: the decision happens at fixture setup, so the test
    body — and the worktree it would create — is never reached."""
    import conftest

    monkeypatch.setattr(conftest, "filesystem_is_case_insensitive", lambda path: False)
    with pytest.raises(Exception) as caught:
        next(conftest._case_insensitive_filesystem_impl(pathlib.Path.cwd()))
    assert caught.typename == "Skipped"
    assert "case" in str(caught.value).lower()


# ---------------------------------------------------------------------------
# The meta-guard, so this cannot come back
# ---------------------------------------------------------------------------

# Sites where the expensive call IS the probe's own input, so it cannot be
# hoisted. Each entry needs a reason, because an unexplained allowlist is how a
# guard stops being one.
SKIP_AFTER_SETUP_ALLOWLIST = {
    # The probe runs `stat` on this file, so the file has to exist first. It is
    # one small write into tmp_path and nothing outside it is touched.
    ("test_session_reload_mtime.py", "test_the_old_probe_chain_is_unstable_on_this_platform"),
}

# Calls that create state a runner would have to clean up, or that cost real time.
_EXPENSIVE = frozenset(
    {
        "create_worktree",
        "new_session",
        "mkdtemp",
        "mkdir",
        "makedirs",
        "clone",
        "init_repo",
        "Popen",
        "check_output",
        "write_text",
        "write_bytes",
        "symlink_to",
        "TemporaryDirectory",
        "NamedTemporaryFile",
        "copytree",
        "copy2",
    }
)


def _call_name(node: ast.Call) -> str | None:
    return getattr(node.func, "attr", None) or getattr(node.func, "id", None)


def _spans_with_guaranteed_cleanup(fn: ast.AST) -> list[tuple[int, int]]:
    """Line spans of ``try`` bodies whose ``finally`` always runs.

    A skip raised inside one of these is fine however much was built first: the
    ``finally`` executes as the ``Skipped`` exception propagates, so the cleanup
    is unconditional. That is the second legitimate shape, alongside "probe
    before you build", and the rule has to accept both or it would forbid the
    correct fix to its own findings.

    Structure only. That the ``finally`` cleans up the RIGHT things is a semantic
    claim no AST walk can make, which is why the behavioural tests at the top of
    this file exist — the two guards are complementary, not redundant.
    """
    spans: list[tuple[int, int]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and node.finalbody and node.body:
            start = min(stmt.lineno for stmt in node.body)
            end = max(getattr(inner, "lineno", start) for stmt in node.body for inner in ast.walk(stmt))
            spans.append((start, end))
    return spans


def _skip_after_setup_sites(directory: pathlib.Path | None = None) -> list[tuple[str, str, int, tuple[str, ...]]]:
    """Every function that calls ``pytest.skip`` after an expensive call.

    ``directory`` is injectable so the scanner can be pointed at a planted
    violation, which is the only way to prove it is capable of finding one.
    """
    directory = TESTS_DIR if directory is None else directory
    findings: list[tuple[str, str, int, tuple[str, ...]]] = []
    candidates = sorted(directory.glob("test_*.py"))
    conftest = directory / "conftest.py"
    if conftest.exists():
        candidates.append(conftest)
    for path in candidates:
        if path.name == pathlib.Path(__file__).name:
            continue  # this file's allowlist mentions the names it guards
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            skips: list[int] = []
            work: list[tuple[int, str]] = []
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node)
                if name == "skip":
                    skips.append(node.lineno)
                elif name in _EXPENSIVE:
                    work.append((node.lineno, name))
            protected = _spans_with_guaranteed_cleanup(fn)
            for skip_line in skips:
                if any(start <= skip_line <= end for start, end in protected):
                    continue  # a finally will run, so nothing is left behind
                earlier = tuple(sorted({n for line, n in work if line < skip_line}))
                if earlier:
                    findings.append((path.name, fn.name, skip_line, earlier))
    return findings


def test_given_the_suite_when_scanned_then_no_test_skips_after_doing_expensive_setup():
    """A durable guard, because the four original sites were all written the same
    wrong way independently — which means the shape is attractive, not accidental.
    """
    offenders = [
        (module, fn, line, calls)
        for module, fn, line, calls in _skip_after_setup_sites()
        if (module, fn) not in SKIP_AFTER_SETUP_ALLOWLIST
    ]
    assert not offenders, "probe before you build:\n" + "\n".join(
        f"  {m}:{line} {fn} skips after {list(calls)}" for m, fn, line, calls in offenders
    )


def test_given_the_allowlist_when_checked_then_every_entry_is_still_a_real_site():
    """An allowlist entry that no longer matches anything is a stale exemption,
    and the next genuine offender in that file would inherit its cover."""
    live = {(module, fn) for module, fn, _line, _calls in _skip_after_setup_sites()}
    stale = SKIP_AFTER_SETUP_ALLOWLIST - live
    assert not stale, f"remove these allowlist entries, they match nothing: {sorted(stale)}"


def test_given_the_scanner_when_run_then_it_can_actually_find_a_violation(tmp_path):
    """Positive control for the guard above. An AST walk that silently matched
    nothing would make that test pass forever while measuring zero lines."""
    (tmp_path / "test_planted_violation.py").write_text(
        "import pytest, tempfile\ndef test_planted():\n    tempfile.mkdtemp()\n    pytest.skip('too late')\n",
        encoding="utf-8",
    )

    found = _skip_after_setup_sites(tmp_path)

    assert any(fn == "test_planted" and "mkdtemp" in calls for _m, fn, _line, calls in found), found


def test_given_a_correctly_ordered_probe_when_scanned_then_it_is_not_flagged(tmp_path):
    """Negative control: the scanner must not simply flag everything."""
    (tmp_path / "test_planted_ok.py").write_text(
        "import pytest, tempfile\ndef test_planted_ok():\n    pytest.skip('probed first')\n    tempfile.mkdtemp()\n",
        encoding="utf-8",
    )

    assert _skip_after_setup_sites(tmp_path) == []


def test_given_a_skip_inside_a_try_finally_when_scanned_then_it_is_not_flagged(tmp_path):
    """The other legitimate shape: build first, but guarantee the cleanup.

    Without this the rule would forbid the correct fix to its own findings, which
    is how a guard ends up being worked around rather than satisfied.
    """
    (tmp_path / "test_planted_guarded.py").write_text(
        "import pytest, shutil, tempfile\n"
        "def test_planted_guarded():\n"
        "    d = tempfile.mkdtemp()\n"
        "    try:\n"
        "        pytest.skip('cleanup is unconditional')\n"
        "    finally:\n"
        "        shutil.rmtree(d, ignore_errors=True)\n",
        encoding="utf-8",
    )

    assert _skip_after_setup_sites(tmp_path) == []


def test_given_a_try_with_no_finally_when_scanned_then_it_is_still_flagged(tmp_path):
    """A bare `try/except` proves nothing about cleanup, so it must not exempt."""
    (tmp_path / "test_planted_unguarded.py").write_text(
        "import pytest, tempfile\n"
        "def test_planted_unguarded():\n"
        "    tempfile.mkdtemp()\n"
        "    try:\n"
        "        pytest.skip('no finally here')\n"
        "    except ValueError:\n"
        "        pass\n",
        encoding="utf-8",
    )

    found = _skip_after_setup_sites(tmp_path)

    assert any(fn == "test_planted_unguarded" for _m, fn, _line, _calls in found), found


def test_shutil_which_is_the_cheap_probe_for_a_missing_binary():
    """The hoisted `script`/`zsh` checks are pure PATH lookups, so there is never
    a reason to build anything before them."""
    assert shutil.which("definitely-not-a-real-binary-xyzzy") is None
