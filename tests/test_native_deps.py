"""A native binary that resolves and then dies on a missing shared library.

THE BUG (AI-CLI-i2ih / AI-CLI-d89q). A tmux built into a persistent per-user
prefix linked against a shared library that lived in the *container* image. When
the machine restarted, the container filesystem was rebuilt and the library went
with it, leaving the binary in place and unrunnable::

    $ tmux -V
    tmux: error while loading shared libraries: libevent_core-2.1.so.7:
    cannot open shared object file: No such file or directory
    exit=127

The launcher treated "on PATH" as "usable", so it reported ``launching inside
tmux``, synchronized a worktree, and only then died on that same loader error.

The library was not actually gone: a copy was sitting in a *persistent*
directory one level below the same prefix, and the binary ran the moment the
loader was pointed at it. So the repair is discovery, not installation — which
is what this module's helpers do, and why they are written against the loader's
own error text rather than against tmux.

These tests drive a real child process through a real dynamic-loader search
path. Mocking ``subprocess.run`` here would test the regex and nothing else,
and the regex was never the part that was wrong.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ai_cli import native_deps

# The soname the fake binary below demands. Deliberately shaped like a real one
# (a versioned suffix after the ``.so``), because that is the form the loader
# reports and the form that has to survive being used as a filename.
FAKE_SONAME = "libfake_probe-1.2.so.3"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "LD_LIBRARY_PATH/DYLD_LIBRARY_PATH and the loader's error text are POSIX "
        "loader behaviour; Windows resolves DLLs through PATH and has no "
        "equivalent search variable (see test_win32_has_no_loader_search_path)"
    ),
)


# ---------------------------------------------------------------------------
# Parsing the loader's own words
# ---------------------------------------------------------------------------


class TestMissingSharedLibraries:
    """Three loaders, three wordings, one answer.

    The name is taken from the loader's message rather than from ``ldd`` on
    purpose: ``ldd`` is absent on musl and on macOS, and the process that just
    failed has already told us exactly what it wanted.
    """

    def test_given_a_glibc_loader_error_when_parsed_then_the_soname_is_returned(self):
        stderr = (
            "tmux: error while loading shared libraries: libevent_core-2.1.so.7: "
            "cannot open shared object file: No such file or directory"
        )
        assert native_deps.missing_shared_libraries(stderr) == ("libevent_core-2.1.so.7",)

    def test_given_a_musl_loader_error_when_parsed_then_the_soname_is_returned(self):
        stderr = "Error loading shared library libevent_core-2.1.so.7: No such file or directory (needed by /bin/tmux)"
        assert native_deps.missing_shared_libraries(stderr) == ("libevent_core-2.1.so.7",)

    def test_given_a_macos_dyld_error_when_parsed_then_the_basename_is_returned(self):
        """dyld names a full install path; only the filename can be searched for."""
        stderr = (
            "dyld[123]: Library not loaded: /opt/homebrew/opt/libevent/lib/libevent_core-2.1.7.dylib\n"
            "  Referenced from: <UUID> /opt/homebrew/bin/tmux\n"
            "  Reason: tried: '/opt/homebrew/opt/libevent/lib/...' (no such file)"
        )
        assert native_deps.missing_shared_libraries(stderr) == ("libevent_core-2.1.7.dylib",)

    def test_given_several_missing_libraries_when_parsed_then_each_appears_once(self):
        """The loader stops at the first one, but a retry surfaces the next."""
        stderr = (
            "error while loading shared libraries: libone.so.1: cannot open shared object file\n"
            "error while loading shared libraries: libtwo.so.2: cannot open shared object file\n"
            "error while loading shared libraries: libone.so.1: cannot open shared object file"
        )
        assert native_deps.missing_shared_libraries(stderr) == ("libone.so.1", "libtwo.so.2")

    def test_given_an_unrelated_failure_when_parsed_then_nothing_is_claimed_missing(self):
        """A wrong diagnosis is worse than none: it would send the repair hunting
        for a library that was never the problem, then report a bogus remedy."""
        assert native_deps.missing_shared_libraries("tmux: unknown option -- z") == ()
        assert native_deps.missing_shared_libraries("") == ()
        assert native_deps.missing_shared_libraries("Segmentation fault") == ()


# ---------------------------------------------------------------------------
# Where a replacement is looked for
# ---------------------------------------------------------------------------


@pytest.fixture
def prefix(tmp_path: Path) -> Path:
    """A ``bin``/``lib`` prefix shaped like the machine the defect was found on.

    ``<prefix>/lib/payload/usr/lib`` is the layout an extracted self-contained
    bundle leaves behind, and on the affected machine it is where the surviving
    copy of the library actually was. A search that only looked at
    ``<prefix>/lib`` would have found nothing and reported the box unfixable.
    """
    (tmp_path / "bin").mkdir()
    (tmp_path / "lib" / "payload" / "usr" / "lib").mkdir(parents=True)
    return tmp_path


def _write_exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_binary(prefix: Path, name: str = "fake-probe") -> Path:
    """A binary that fails exactly the way the broken tmux failed.

    It succeeds only when the loader search path actually contains the library,
    so nothing about this test can pass on a repair that merely *claims* to have
    worked. POSIX ``sh`` so it does not depend on bash being present.
    """
    var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    return _write_exe(
        prefix / "bin" / name,
        f"""#!/bin/sh
IFS=':'
for dir in ${{{var}:-}}; do
    if [ -f "$dir/{FAKE_SONAME}" ]; then
        echo "fake-probe 1.0"
        exit 0
    fi
done
echo "{name}: error while loading shared libraries: {FAKE_SONAME}: \
cannot open shared object file: No such file or directory" >&2
exit 127
""",
    )


class TestCandidateLibraryDirs:
    def test_given_a_binary_in_bin_when_searched_then_the_sibling_lib_dirs_are_candidates(self, prefix: Path):
        exe = _fake_binary(prefix)
        dirs = native_deps.candidate_library_dirs(exe)
        assert prefix / "lib" in dirs

    def test_given_a_nested_bundle_payload_when_searched_then_its_lib_dir_is_a_candidate(self, prefix: Path):
        """The measured case. This one directory is the whole fix on that box."""
        exe = _fake_binary(prefix)
        assert prefix / "lib" / "payload" / "usr" / "lib" in native_deps.candidate_library_dirs(exe)

    def test_given_a_symlinked_binary_when_searched_then_the_real_prefix_is_searched_too(self, tmp_path: Path):
        """A launcher symlink in ``~/.local/bin`` pointing into an unpacked tree
        is a normal install shape, and its libraries live by the real file."""
        real = tmp_path / "real"
        (real / "bin").mkdir(parents=True)
        (real / "lib").mkdir()
        _write_exe(real / "bin" / "fake-probe", "#!/bin/sh\nexit 0\n")
        link_bin = tmp_path / "link" / "bin"
        link_bin.mkdir(parents=True)
        link = link_bin / "fake-probe"
        link.symlink_to(real / "bin" / "fake-probe")

        assert real / "lib" in native_deps.candidate_library_dirs(link)

    def test_given_no_candidate_dir_exists_when_searched_then_none_are_returned(self, tmp_path: Path):
        """Never hand the loader a path that is not there — a nonexistent entry
        in the search variable is silent, so it would look like a real repair."""
        (tmp_path / "bin").mkdir()
        exe = _write_exe(tmp_path / "bin" / "fake-probe", "#!/bin/sh\nexit 0\n")
        assert native_deps.candidate_library_dirs(exe) == ()

    def test_given_an_explicit_override_when_searched_then_it_is_tried_first(self, prefix: Path, monkeypatch):
        """The escape hatch for a layout this heuristic does not know about."""
        override = prefix / "elsewhere"
        override.mkdir()
        monkeypatch.setenv(native_deps.LIBRARY_PATH_OVERRIDE, str(override))
        exe = _fake_binary(prefix)

        dirs = native_deps.candidate_library_dirs(exe)

        assert dirs[0] == override

    def test_given_an_override_naming_a_missing_dir_when_searched_then_it_is_dropped(self, prefix: Path, monkeypatch):
        monkeypatch.setenv(native_deps.LIBRARY_PATH_OVERRIDE, str(prefix / "nope"))
        assert prefix / "nope" not in native_deps.candidate_library_dirs(_fake_binary(prefix))


class TestFindLibraryDirs:
    def test_given_the_library_exists_in_a_candidate_when_searched_then_that_dir_is_returned(self, prefix: Path):
        payload = prefix / "lib" / "payload" / "usr" / "lib"
        (payload / FAKE_SONAME).write_bytes(b"")
        exe = _fake_binary(prefix)

        dirs, unresolved = native_deps.find_library_dirs(exe, [FAKE_SONAME])

        assert dirs == (payload,)
        assert unresolved == ()

    def test_given_the_library_is_nowhere_when_searched_then_it_is_reported_unresolved(self, prefix: Path):
        """Honest partial failure. Silently returning the dirs it *did* find
        would produce a repair that cannot work and a success report."""
        dirs, unresolved = native_deps.find_library_dirs(_fake_binary(prefix), [FAKE_SONAME])

        assert dirs == ()
        assert unresolved == (FAKE_SONAME,)

    def test_given_two_libraries_in_one_dir_when_searched_then_the_dir_appears_once(self, prefix: Path):
        payload = prefix / "lib" / "payload" / "usr" / "lib"
        (payload / FAKE_SONAME).write_bytes(b"")
        (payload / "libother.so.9").write_bytes(b"")

        dirs, unresolved = native_deps.find_library_dirs(_fake_binary(prefix), [FAKE_SONAME, "libother.so.9"])

        assert dirs == (payload,)
        assert unresolved == ()


# ---------------------------------------------------------------------------
# The repair itself, against a real child process
# ---------------------------------------------------------------------------


@pytest.fixture
def on_path(prefix: Path, monkeypatch):
    """Put the fake prefix's ``bin`` first on PATH and hand back a real env copy.

    The env is a copy of ``os.environ`` rather than ``os.environ`` itself so a
    test cannot leak a loader path into the rest of the suite, and it is a real
    full environment so the child process can actually start.

    The loader variable is cleared from the copy. The ambient environment may
    already carry a repair — this suite's own ``conftest.tmux_runnable`` performs
    one on a host with a broken tmux — and inheriting it made "the variable is
    unset afterwards" assertions pass or fail depending on which other test
    modules happened to be collected into the same worker.
    """
    monkeypatch.setenv("PATH", f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    env = dict(os.environ)
    env.pop(native_deps.loader_path_var() or "", None)
    return env


def _library_in_payload(prefix: Path) -> Path:
    payload = prefix / "lib" / "payload" / "usr" / "lib"
    (payload / FAKE_SONAME).write_bytes(b"")
    return payload


class TestRepairLoaderPath:
    def test_given_a_binary_broken_by_a_missing_library_when_repaired_then_it_runs(self, prefix: Path, on_path):
        """The whole defect, end to end, through a real loader.

        Positive control first: the binary must genuinely be broken before the
        repair, or a no-op implementation would pass this.
        """
        _fake_binary(prefix)
        payload = _library_in_payload(prefix)

        before = subprocess.run(["fake-probe"], capture_output=True, text=True, check=False)
        assert before.returncode != 0, "the fake binary must start out broken"
        assert FAKE_SONAME in before.stderr

        repair = native_deps.repair_loader_path(["fake-probe"], env=on_path)

        assert repair.repaired is True
        assert repair.missing == (FAKE_SONAME,)
        assert str(payload) in repair.added_dirs
        # And the environment it mutated really does make the binary run.
        after = subprocess.run(["fake-probe"], capture_output=True, text=True, check=False, env=on_path)
        assert after.returncode == 0
        assert "fake-probe 1.0" in after.stdout

    def test_given_a_repair_when_it_lands_then_it_prepends_and_keeps_the_existing_path(self, prefix: Path, on_path):
        """Clobbering the operator's own loader path would break other binaries."""
        var = native_deps.loader_path_var()
        on_path[var] = "/opt/existing/lib"
        _fake_binary(prefix)
        payload = _library_in_payload(prefix)

        assert native_deps.repair_loader_path(["fake-probe"], env=on_path).repaired is True

        entries = on_path[var].split(os.pathsep)
        assert entries[0] == str(payload)
        assert "/opt/existing/lib" in entries

    def test_given_the_library_cannot_be_found_when_repaired_then_it_reports_what_is_missing(
        self, prefix: Path, on_path
    ):
        """AC-2: the operator gets the soname and the dirs that were searched,
        not a bare 'tmux is broken'."""
        _fake_binary(prefix)

        repair = native_deps.repair_loader_path(["fake-probe"], env=on_path)

        assert repair.repaired is False
        assert repair.missing == (FAKE_SONAME,)
        assert repair.unresolved == (FAKE_SONAME,)
        assert FAKE_SONAME in repair.detail

    def test_given_a_failed_repair_when_it_returns_then_the_environment_is_untouched(self, prefix: Path, on_path):
        """A speculative loader path left behind changes how every later child
        process resolves its libraries. Wrong guesses must not persist."""
        var = native_deps.loader_path_var()
        on_path.pop(var, None)
        _fake_binary(prefix)
        # A library that exists but does not satisfy the binary: the dir is
        # found, the path is set, and the binary still fails. This is the case
        # that must roll back.
        (prefix / "lib" / FAKE_SONAME).write_bytes(b"")
        _write_exe(
            prefix / "bin" / "fake-probe",
            f"""#!/bin/sh
echo "fake-probe: error while loading shared libraries: {FAKE_SONAME}: \
cannot open shared object file: No such file or directory" >&2
exit 127
""",
        )

        repair = native_deps.repair_loader_path(["fake-probe"], env=on_path)

        assert repair.repaired is False
        assert var not in on_path, f"{var} was left set to a path that does not work"

    def test_given_a_binary_that_already_runs_when_repaired_then_nothing_changes(self, prefix: Path, on_path):
        var = native_deps.loader_path_var()
        _write_exe(prefix / "bin" / "fake-probe", "#!/bin/sh\necho ok\nexit 0\n")

        repair = native_deps.repair_loader_path(["fake-probe"], env=on_path)

        assert repair.repaired is False
        assert repair.missing == ()
        assert var not in on_path

    def test_given_a_failure_that_is_not_a_loader_error_when_repaired_then_it_is_left_alone(
        self, prefix: Path, on_path
    ):
        """A crash, a bad flag or a permissions error is not ours to fix, and
        the report must carry the real message instead of inventing one."""
        _write_exe(prefix / "bin" / "fake-probe", "#!/bin/sh\necho 'fake-probe: unknown option -- z' >&2\nexit 2\n")

        repair = native_deps.repair_loader_path(["fake-probe"], env=on_path)

        assert repair.repaired is False
        assert repair.missing == ()
        assert "unknown option" in repair.detail

    def test_given_the_binary_is_not_on_path_when_repaired_then_it_says_so(self, on_path):
        repair = native_deps.repair_loader_path(["ai-cli-no-such-binary-xyzzy"], env=on_path)

        assert repair.repaired is False
        assert "PATH" in repair.detail

    def test_given_any_failure_mode_when_repaired_then_it_never_raises(self, prefix: Path, on_path):
        """Every caller runs mid-launch. A raising repair IS the outage it
        exists to prevent, which is this module's standing contract."""
        _write_exe(prefix / "bin" / "fake-probe", "not an executable format at all\n")
        assert native_deps.repair_loader_path(["fake-probe"], env=on_path).repaired is False

        empty = prefix / "bin" / "not-executable"
        empty.write_text("#!/bin/sh\nexit 0\n")  # no execute bit
        assert native_deps.repair_loader_path([str(empty)], env=on_path).repaired is False


def test_win32_has_no_loader_search_path():
    """AC-4's half of this: Windows resolves DLLs through PATH, so there is no
    search variable to repair. Saying so beats pretending LD_LIBRARY_PATH means
    something there, and it is why the repair is a no-op rather than a failure."""
    from unittest.mock import patch

    with patch.object(sys, "platform", "win32"):
        assert native_deps.loader_path_var() is None
        repair = native_deps.repair_loader_path(["fake-probe"], env={})
    assert repair.repaired is False
    assert "no dynamic-loader search path" in repair.detail


def test_the_loader_variable_matches_the_platform():
    from unittest.mock import patch

    with patch.object(sys, "platform", "darwin"):
        assert native_deps.loader_path_var() == "DYLD_LIBRARY_PATH"
    with patch.object(sys, "platform", "linux"):
        assert native_deps.loader_path_var() == "LD_LIBRARY_PATH"
