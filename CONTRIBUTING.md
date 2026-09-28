# Contributing to ai-cli-utils

Thanks for your interest in contributing! This guide covers everything you need to get started.

## Development Setup

### One Python version

This project **develops and tests on exactly one** Python version, currently **3.14**, and
`.python-version` is the canonical place it is declared. Every CI job pins 3.14, and `uv sync`
builds the local venv from `.python-version`, so a local run is on the same interpreter CI uses.

That is separate from what an installed copy of the published package supports, which is
`requires-python = ">=3.11"` with no upper bound. Those are two different questions and conflating
them is a real defect: `>=3.14,<3.15` briefly shipped here and would have refused installation for
every user on 3.11–3.13, and refused 3.15 on the day it was released, until somebody cut a new
release. An upper bound cannot be routed around by a resolver, because it is baked into the
published artifact. CI byte-compiles the shipped source on 3.11 so the wider claim is checked
rather than merely asserted.

That is not a style preference. When the local venv ran 3.14 and CI ran 3.11/3.12/3.13, a defect
turned out to be *unobservable* locally rather than merely unobserved — `pathlib` allows a
`WindowsPath` on POSIX on 3.14 and refuses it at or below 3.13, so a test passed every local run
and went red on four CI jobs, and no amount of local care could have caught it.

`scripts/check_python_version_sync.py` enforces this. It runs as a pre-commit hook and as the
first step of CI's lint job, and it fails if `.python-version`, `requires-python`, the pyright
target, the trove classifiers, any workflow's pinned version, or the interpreter running the
check disagree. Changing the supported version means editing `.python-version` and then fixing
everything the check names.

The cost is stated plainly: cross-version coverage is gone. A behaviour that differs between
interpreters will no longer be caught by CI, so when you rely on one, write a test that forces
the behaviour explicitly instead of trusting the interpreter to exhibit it.

```bash
# Clone the repo
git clone https://github.com/sergeiwallace/ai-cli-utils.git # public-hygiene: allow
cd ai-cli-utils

# Create virtual environment and install dev dependencies
# (uv reads .python-version and provisions Python 3.14)
uv sync --dev

# Configure Claude Code session config for your environment
uv run ai setup

# Set up pre-commit hooks (optional but recommended)
uv run pre-commit install

# Verify everything works
uv run python scripts/check_python_version_sync.py
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest
```text

### Relock after changing a dependency

If you edit a dependency in `pyproject.toml`, run `uv lock` and commit the updated `uv.lock` in the
same change. CI syncs with `uv sync --locked`, which refuses to install when the two disagree:

```text
error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.

hint: To update the lockfile, run `uv lock`.
```

Local `uv sync --dev` deliberately keeps re-resolving, so an edit-and-run loop still works without
a relock on every step. CI is the boundary where it has to stop, because a plain `uv sync` there
re-resolved silently: a pull request could pass every check having installed versions that were not
the ones it shipped, and no log line said so. `--frozen` is not a substitute — it installs from the
lock without comparing it to `pyproject.toml`, which hides the disagreement rather than reporting
it. `tests/test_ci_lock_assertion.py` fails if a workflow sync step stops asserting the lock.

## Running Tests

### A test owns every process it starts

A test that spawns a real process must spawn it through `tests/process_ownership.py`, not
through a bare `subprocess.Popen`:

```python
from process_ownership import owned_sleeper, reap, spawn_owned

with owned_sleeper() as proc:      # spawned in its own group, reaped on the way out
    ...

proc = spawn_owned([sys.executable, "-c", "..."])  # when you need the handle yourself
try:
    ...
finally:
    reap(proc)                     # ends the GROUP, not just the direct child
```text

Two reasons it is a helper and not a convention. A child in a group of its own can be ended
as a whole tree, where terminating the direct child leaves its own children running and
reparented. And the platforms genuinely differ: `start_new_session=True` is POSIX and is
silently *ignored* on Windows, where the group has to come from `CREATE_NEW_PROCESS_GROUP`
and there is no `killpg` to signal it with — `os.killpg`, `os.getpgid`, `os.getpgrp` and
`signal.SIGKILL` do not exist there at all. Hand-rolling that split is how a change goes
green locally and takes the Windows jobs red.

Do not rely on `finally:` alone. It does not run when the run is killed, when a suite-level
timeout fires, or when an xdist worker dies, which is why the helper's spawned children also
sleep for a bounded time and exit by themselves. Never clean up with a pattern-matched
`pkill`: it matches processes the suite does not own.

A spawn whose *subject* is the process group — one asserting that a child shares the runner's
group, or whose payload calls `os.setsid()` itself — is the exception, and says so in a
comment at the call site.

### tmux is required, not optional

Install `tmux` before running the suite. It is a hard test dependency: several tests drive
a real tmux server rather than a mock, and they fail rather than skip without it.

```bash
# macOS
brew install tmux

# Debian/Ubuntu
sudo apt-get install tmux

# Windows: via MSYS2, which is what CI uses
pacman -S tmux
```text

Every CI job installs it explicitly, on all three platforms. That is deliberate rather than
belt-and-braces: two runs of the same commit once differed only in whether the runner image
happened to ship tmux, and reported 33 skips / 0 failures versus 45 skips / 3 failures. A
suite whose coverage moves with the host is a suite whose green is not worth much, so the
dependency is guaranteed instead of tolerated.

The suite enforces the other half of that: if tmux IS usable and a test still skips for lack
of it, the run fails at the end with a `tmux coverage regression` summary naming the tests.
A skip count that quietly drifts is exactly what went unnoticed before.

```bash
# Full test suite
uv run pytest

# Verbose output
uv run pytest -v

# Single test file
uv run pytest tests/test_main.py

# Single test
uv run pytest tests/test_main.py::test_remote_flag_when_host_configured_then_sshs_to_host
```text

## Code Style

This project uses [ruff](https://github.com/astral-sh/ruff) for linting and formatting,
pinned to an exact version in `pyproject.toml` and `.pre-commit-config.yaml`. The enabled
rule set is declared explicitly via `[tool.ruff.lint] select`, rather than inherited from
ruff's default — a ruff upgrade is free to change that default, and one did, which would
otherwise silently redefine what the gate enforces.

```bash
# Check lint
uv run ruff check src/ tests/

# Auto-fix lint issues
uv run ruff check --fix src/ tests/

# Check formatting
uv run ruff format --check src/ tests/

# Auto-format
uv run ruff format src/ tests/
```text

### Lint autofix is deliberate, never automatic

The `ruff-check` pre-commit hook runs **without** `--fix`: it reports and fails, and never
rewrites your files. `ruff-format` still formats, because formatting is not scoped to the
rule set.

That asymmetry exists because of what happens when the enabled rule set grows. Widening
`[tool.ruff.lint] select` makes a whole family of findings appear across the codebase at
once. The hook, though, only ever sees the few files a given commit happens to touch — so
with `--fix` the new family would not surface as a reviewable list of findings. It would be
rewritten a few files at a time, buried inside unrelated commits, attributed to whoever was
working on something else. `--fix` does not prevent a mass autofix; it only removes the
review.

So when you enable a new rule family, apply its fixes on purpose, as their own commit:

```bash
# 1. See the full scope before changing anything
uv run ruff check --statistics src/ tests/

# 2. Apply the autofixable subset deliberately
uv run ruff check --fix src/ tests/

# 3. Review that diff on its own, then commit it separately from any feature work
git diff
```text

One reviewable mechanical commit is strictly better than the same edits dribbling through
unrelated ones.

## Hard Gate

All contributions must pass this before merge:

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ \
  && uv run pyright src/ && uv run pytest
```text

**`pyright` is part of the gate, not an extra.** It used to be missing here while CI's
lint job ran it, which meant type errors passed every local check and landed on `main`
with nothing to catch them (AI-CLI-ckzk). That is not a theoretical gap — it was found
by running pyright by hand on a branch whose gate had been reported clean, and it
reported `main.py:3156 - Argument of type "Path | None" cannot be assigned to
parameter "path" of type "Path"`.

It also runs as a `pre-push` pre-commit hook rather than a `pre-commit` one. At around
five seconds it is too slow to pay on every commit and cheap enough to pay before code
leaves the machine, which is also where the defect actually lives: the problem was type
errors reaching `main`, not type errors existing briefly in a local commit.

`tests/test_local_gate_matches_ci_lint.py` keeps the two definitions aligned. Every
check in CI's lint job must be either enforced locally by a named pre-commit hook or
recorded as intentionally CI-only with a reason — adding one to CI without deciding
which fails that test. Two checks are deliberately CI-only: `uv sync --locked` is
environment provisioning rather than a check, and the 3.11 floor `compileall` needs a
second interpreter that every contributor would otherwise have to download to verify a
claim about the published artifact.

**Run it through `uv run`, not a bare `ruff`/`pytest`.** A bare `ruff` resolves through
`PATH`, which may be a different version than `pyproject.toml` pins — and the ruff version
decides the verdict. A venv one minor version behind the pin reported "All checks passed!"
for a tree the pinned binary found 1075 errors in (see
[BUG-006](docs/bugs/ruff-gate-inherited-ruleset.md)). `uv run` syncs the environment to the
lockfile first, so the gate and the pre-commit hook agree.

The `ruff-version-sync` pre-commit hook fails the commit if the installed ruff does not
match the pin, so this can't drift silently again.

## Pull Request Process

1. Fork the repo and create a branch from `main` (`feature/short-description` or `fix/short-description`)
2. Make your changes
3. Ensure the hard gate passes
4. Open a PR against `main`
5. Describe what changed and why in the PR description

## Test Conventions

- Test names follow `test_{given}_{when}_{then}` pattern
- Use pytest fixtures for shared setup
- Mock at system boundaries only (subprocess, filesystem, network)
- Session-launch tests that mock `os.execvp` must also mock `subprocess.run` when the
  path can create or manage a tmux session. The test safety guard rejects unmocked
  `tmux`, `claude`, `gemini`, and `direnv` processes so tests cannot leave live sessions behind.
- Every public function needs at least one failure-path test

## Project Structure

```text
src/ai_cli/
  main.py          # CLI entry point, session management, argparse
  sync.py          # Cross-machine sync (push/pull/watch/conflicts)
  messaging.py     # NATS client for fleet messaging
  memory.py        # Memory file watcher daemon
  quota.py         # API quota tracking
  setup.py         # `ai setup` — environment detection and CLAUDE.md configuration
  telemetry.py     # Usage telemetry
  handoff.py       # Cross-session task handoff queue

tests/
  test_main.py     # Session management tests
  test_sync.py     # Sync tests
  test_messaging.py # NATS messaging tests
  ...
```text

## Reporting Issues

Open an issue on [GitHub Issues](https://github.com/sergeiwallace/ai-cli-utils/issues) with: <!-- public-hygiene: allow -->
- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS
