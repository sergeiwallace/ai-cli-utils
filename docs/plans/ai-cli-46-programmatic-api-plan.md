---
title: "AI-CLI-46: Stabilize Programmatic API for aido Integration"
category: plan
tags: [api, programmatic, aido, versioning]
status: approved
---

## Table of Contents

- [Context](#context)
- [Public API Surface](#public-api-surface)
  - [gemini module](#gemini-module)
  - [quota module](#quota-module)
- [Implementation Steps](#implementation-steps)
- [Version Floor for AIDO-18](#version-floor-for-aido-18)
- [Verification](#verification)
- [Approval Log](#approval-log)

## Context

AIDO-18 (aido's Gemini call routing through ai-cli-utils) requires a stable, versioned public API that aido can pin against in its `pyproject.toml`. Without an explicit API contract, any ai-cli-utils refactor risks silent breakage in aido's import paths.

AI-CLI-46 defines that contract: specific functions are promoted to public API via `__all__`, their signatures are documented here, and a release is cut so aido can set `ai-cli-utils>=X.Y.0` with confidence.

Most of the implementation was already completed (see Current State below). The remaining work is fixing a `__init__.__version__` mismatch and cutting the release.

### Current State at Planning Time

| Item | Status |
|------|--------|
| `gemini.__all__ = ["GeminiResult", "AttemptLog", "run_gemini"]` | ✅ shipped in v0.5.x |
| `quota.__all__ = ["QuotaSnapshot", "read_latest_snapshot"]` | ✅ shipped in v0.5.x |
| `read_latest_snapshot()` — reads SQLite, no tmux scraping | ✅ shipped in v0.5.x |
| `tests/test_public_api.py` — 6 smoke tests | ✅ shipped in v0.5.x |
| `__init__.__version__` synced to pyproject.toml | ❌ bug: `0.3.0` vs `0.5.1` |

## Public API Surface

Functions and types listed here are stable public API. Changes to these signatures require a minor version bump. Internal helpers (prefixed `_`) are not covered.

### gemini module

```python
from ai_cli.gemini import run_gemini, GeminiResult, AttemptLog
```

```python
def run_gemini(
    prompt: str,
    *,
    model: str = "deep-think",
    output: str | None = None,
    quiet: bool = False,
    verbose: bool = False,
    timeout_s: int = 600,
    start_tier: int = 1,
    paid_fallback_enabled: bool | None = None,
) -> GeminiResult: ...
```

```python
@dataclass
class GeminiResult:
    content: str = ""
    model: str = ""
    tier: int = 0
    tier_name: str = ""
    success: bool = False
    error: str = ""
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    is_deep_research: bool = False
    attempts: list = field(default_factory=list)
    event_id: str = ""
    machine: str = ""

@dataclass
class AttemptLog:
    tier: int
    tier_name: str
    model: str
    success: bool
    error: str = ""
    duration_ms: int = 0
```

### quota module

```python
from ai_cli.quota import read_latest_snapshot, QuotaSnapshot
```

```python
def read_latest_snapshot() -> QuotaSnapshot | None:
    """Return the most recent stored quota snapshot from local SQLite.

    Reads from the local DB without scraping — fast and safe to call from
    library code. Returns None if no snapshots have been recorded yet.
    """

@dataclass
class QuotaSnapshot:
    weekly_all_models_pct: float
    session_pct: float | None = None
    weekly_sonnet_pct: float | None = None
    extra_pct: float | None = None
    reset_at: str | None = None
```

## Implementation Steps

1. **Fix `__init__.__version__`** — `src/ai_cli/__init__.py` has `"0.3.0"`; update to match pyproject.toml (`"0.5.1"`).
2. **Run automated checks** — `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest`.
3. **Patch bump to v0.5.2** — bug fix only; update `pyproject.toml` + `__init__.py` + `CHANGELOG.md`.
4. **Commit, tag `v0.5.2`, push** — GH Release workflow fires on tag push.
5. **Publish to PyPI** — `uv publish`.
6. **Post version floor** — `ai-cli-utils>=0.5.2` is the pin floor for AIDO-18.

## Version Floor for AIDO-18

Once v0.5.2 is published, aido's `pyproject.toml` should add:

```toml
[project]
dependencies = [
    "ai-cli-utils>=0.5.2",
]
```

And AIDO-18 import paths:

```python
from ai_cli.gemini import run_gemini, GeminiResult
from ai_cli.quota import read_latest_snapshot, QuotaSnapshot
```

## Verification

1. `python -c "from ai_cli import __version__; assert __version__ == '0.5.2'"` — no mismatch
2. `pytest tests/test_public_api.py -v` — all 6 smoke tests pass
3. `pytest` — full suite passes (≥1655 tests)
4. `pip install ai-cli-utils==0.5.2` — installs and imports cleanly

## Approval Log

- 2026-04-21, Round 1: Plan approved by user. Proceed with implementation.
