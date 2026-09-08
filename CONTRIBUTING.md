# Contributing to ai-cli-utils

Thanks for your interest in contributing! This guide covers everything you need to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/sergeiwallace/ai-cli-utils.git # public-hygiene: allow
cd ai-cli-utils

# Create virtual environment and install dev dependencies
uv sync --dev

# Configure Claude Code session config for your environment
uv run ai setup

# Set up pre-commit hooks (optional but recommended)
uv run pre-commit install

# Verify everything works
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest
```text

## Running Tests

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
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest
```text

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
