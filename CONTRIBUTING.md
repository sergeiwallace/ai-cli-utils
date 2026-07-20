# Contributing to ai-cli-utils

Thanks for your interest in contributing! This guide covers everything you need to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/sergeiwallace/ai-cli-utils.git
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

This project uses [ruff](https://github.com/astral-sh/ruff) for linting and formatting.

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

## Hard Gate

All contributions must pass this before merge:

```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && pytest
```text

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

Open an issue on [GitHub Issues](https://github.com/sergeiwallace/ai-cli-utils/issues) with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS
