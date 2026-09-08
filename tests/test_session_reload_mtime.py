"""The stable-script hot-reload trigger must compare mtimes, not filesystem stats.

Root cause of the endless ``ai-cli session script updated — reloading...`` loop,
measured on Linux (GNU coreutils 9.x) 2026-09-06.

The template probed the stable script's mtime with a macOS-first fallback chain::

    stat -f "%m" "$p" 2>/dev/null || stat -c "%Y" "$p" 2>/dev/null || echo "0"

On BSD/macOS ``stat -f "%m"`` is the mtime and the chain short-circuits correctly.
On GNU coreutils ``-f`` means *filesystem status* and ``%m`` is not a format token
there, so ``%m`` is consumed as a FILENAME. Measured behaviour of that one command:

* stdout carries a multi-line **filesystem report for the real file**,
* stderr carries ``cannot read file system information for '%m'`` (swallowed by
  ``2>/dev/null``),
* and it exits **1**.

The non-zero exit is what makes it dangerous rather than merely wrong: the ``||``
runs the GNU branch too, so the captured value is the filesystem report
*concatenated with* the real mtime. That blob embeds the live
``Blocks: ... Free:`` and ``Inodes: ... Free:`` counters, so two probes taken
seconds apart differ whenever anything on the box allocates or frees a block --
which is continuously. The loop-top comparison therefore fired on essentially
every iteration: print the reload line, ``exit 78``, supervisor relaunches, repeat
forever until the operator pressed Ctrl+C (whose escape path short-circuits the
check).

Two properties the fix must have, and both are asserted below:

1. **The GNU branch is tried first**, so the common Linux case never runs the
   command that exits non-zero with output on stdout.
2. **The result is validated as an integer and the comparison fails CLOSED.** An
   unknown mtime must mean "do not reload", never "reload". A probe that cannot
   answer is exactly the case that produced an infinite loop, so treating its
   answer as "changed" is the bug, not a conservative default.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_cli.session_script import get_engine_script

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="requires bash")


def _script(engine: str = "c") -> str:
    return get_engine_script(
        engine,
        "job-1",
        "c-job-1",
        "c-job-",
        "job",
        worktree_dir="/tmp/wt",
        project_name="myproject",
    )


def _mtime_helper(script: str) -> str:
    """The template's own mtime helper, extracted for direct execution."""
    match = re.search(r"^\s*_file_mtime\(\) \{.*?^\s*\}$", script, re.M | re.S)
    assert match is not None, "the template defines no _file_mtime helper"
    return match.group()


def _run(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ},
    )


# --------------------------------------------------------------------------
# The reproduction: what the OLD probe actually returns on this platform.
# --------------------------------------------------------------------------


def test_the_old_probe_chain_is_unstable_on_this_platform(tmp_path: Path) -> None:
    """A control, not a regression guard: it pins WHY the old idiom looped.

    Skips on a platform where the old chain happens to be sound (macOS), because
    there the bug genuinely does not exist and asserting it would be false.
    """
    target = tmp_path / "session.sh"
    target.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    old = f'stat -f "%m" {target} 2>/dev/null || stat -c "%Y" {target} 2>/dev/null || echo "0"'
    probe = _run(f'v=$({old}); printf "%s" "$v"')
    if "\n" not in probe.stdout:
        pytest.skip("this platform's `stat -f %m` is a real mtime (BSD/macOS)")

    # Multi-line means the filesystem report leaked into the value, and the
    # report carries live free-space counters -- the instability itself.
    assert "Free:" in probe.stdout, probe.stdout
    assert "Blocks:" in probe.stdout, probe.stdout


# --------------------------------------------------------------------------
# The helper's own contract.
# --------------------------------------------------------------------------


def test_helper_returns_a_bare_integer_for_a_real_file(tmp_path: Path) -> None:
    target = tmp_path / "session.sh"
    target.write_text("x", encoding="utf-8")
    helper = _mtime_helper(_script())

    result = _run(f"{helper}\n_file_mtime {target}")

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"\d+", result.stdout), repr(result.stdout)
    assert int(result.stdout) == int(target.stat().st_mtime)


def test_helper_is_stable_across_repeated_probes(tmp_path: Path) -> None:
    """The property the old chain violated: same file, same answer."""
    target = tmp_path / "session.sh"
    target.write_text("x", encoding="utf-8")
    helper = _mtime_helper(_script())

    result = _run(
        f"{helper}\n"
        f"a=$(_file_mtime {target}); : > {tmp_path / 'churn'}; "
        f'b=$(_file_mtime {target}); [[ "$a" == "$b" ]] && printf stable'
    )

    assert result.stdout == "stable", result.stdout + result.stderr


def test_helper_returns_empty_for_a_missing_file(tmp_path: Path) -> None:
    """Empty, not "0": "0" is a value that compares unequal to a real mtime."""
    helper = _mtime_helper(_script())

    result = _run(f'{helper}\nprintf "[%s]" "$(_file_mtime {tmp_path / "nope"})"')

    assert result.stdout == "[]", result.stdout + result.stderr


def test_helper_detects_a_real_change(tmp_path: Path) -> None:
    """The positive control: without it every assertion above passes on a stub
    that always returns the empty string."""
    target = tmp_path / "session.sh"
    target.write_text("x", encoding="utf-8")
    helper = _mtime_helper(_script())

    result = _run(
        f"{helper}\n"
        f'a=$(_file_mtime {target}); touch -d "@1000000000" {target}; '
        f'b=$(_file_mtime {target}); [[ -n "$a" && -n "$b" && "$a" != "$b" ]] '
        f"&& printf changed"
    )

    assert result.stdout == "changed", result.stdout + result.stderr


# --------------------------------------------------------------------------
# The template must USE the helper, and must fail closed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["c", "g", "p"])
def test_no_template_still_carries_the_broken_probe(engine: str) -> None:
    """The BSD form is fine as a FALLBACK; what is forbidden is putting it first
    in an `||` chain, where its GNU non-zero exit makes both answers accumulate."""
    script = _script(engine)
    assert "_file_mtime" in script
    # Scan CODE only. The fix's own comment quotes the broken chain to explain
    # it, and a grep cannot tell an occurrence from a prohibition of one.
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert 'stat -c "%Y"' in code, "positive control: the probe must still be here"
    # `|| _fm=""` after the BSD form is the FIX: it discards a failed result.
    # What must never appear is a fall-through to another PRODUCING command,
    # because GNU's non-zero exit then leaves both answers on stdout.
    assert not re.search(r'stat -f "%m"[^\n]*\|\|\s*(stat|echo|printf)', code), (
        'on GNU coreutils `stat -f "%m" FILE` prints a filesystem report on '
        "stdout and exits 1, so a producing command to the right of `||` runs as "
        "well and the captured value is both answers concatenated"
    )
    # And whatever the BSD form returns must be validated, not trusted.
    assert re.search(r'_fm=\$\(stat -f "%m"[^\n]*\)\s*\|\|\s*_fm=""', code)
    assert code.count("=~ ^[0-9]+$") >= 2, "both the GNU and the BSD answer must be range-checked before use"
    # And it must be reached only after the GNU form has been tried and rejected.
    gnu = code.index('stat -c "%Y"')
    bsd = code.index('stat -f "%m"')
    assert gnu < bsd, "the GNU form must be probed first on a GNU-coreutils fleet"


@pytest.mark.parametrize("engine", ["c", "g", "p"])
def test_the_reload_comparison_requires_both_values_to_be_known(engine: str) -> None:
    """Fail closed: an unknown mtime must never be read as "changed"."""
    script = _script(engine)
    guard = '[[ -n "$_cur_mtime" && -n "$_script_start_mtime" && "$_cur_mtime" != "$_script_start_mtime" ]]'
    assert guard in script, (
        "the reload must be gated on BOTH values being known; an unmeasurable "
        "mtime is what produced the infinite reload loop"
    )
    # And the reload line must be unreachable except through that guard.
    reload_line = 'echo "ai-cli session script updated — reloading..."'
    assert script.count(reload_line) == 1
    guard_at = script.index(guard)
    assert guard_at < script.index(reload_line)


def test_the_baseline_and_the_loop_probe_use_the_same_helper() -> None:
    """Two different probes of the same quantity is how the drift got in."""
    script = _script()
    assert '_script_start_mtime=$(_file_mtime "$_script_stable_path")' in script
    assert '_cur_mtime=$(_file_mtime "$_script_stable_path")' in script
