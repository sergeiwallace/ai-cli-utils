"""Gemini research wrapper with 3-tier auth fallback.

Fallback chain:
  1. gemini CLI (OAuth — free, Google AI subscription)
  2. Gemini REST API with free-tier key (GOOGLE_API_KEY_FREE_TIER)
  3. Gemini REST API with paid tier-1 key (GOOGLE_API_KEY_TIER_1)

Usage:
  ai gemini "prompt" -m deep-think
  ai gemini "prompt" -m gemini-3.1-pro-preview -o output.md
  ai gemini "prompt" -m gemini-3-flash-preview --quiet
  cat prompt.txt | ai gemini -m deep-think -o output.md
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Model aliases for convenience
MODEL_ALIASES = {
    "pro": "gemini-3.1-pro-preview",
    "flash": "gemini-3-flash-preview",
    "flash-lite": "gemini-3.1-flash-lite-preview",
}

# Tier names for logging
TIER_NAMES = {
    1: "gemini-cli (OAuth)",
    2: "API free-tier",
    3: "API paid tier-1",
}

# Default output directory for auto-generated output files
DEFAULT_OUTPUT_DIR = Path.home() / ".local" / "state" / "ai-cli" / "gemini-output"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class GeminiResult:
    """Result of a Gemini API call."""

    content: str = ""
    model: str = ""
    tier: int = 0
    tier_name: str = ""
    success: bool = False
    error: str = ""
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    attempts: list = field(default_factory=list)


@dataclass
class AttemptLog:
    """Log entry for a single attempt."""

    tier: int
    tier_name: str
    model: str
    success: bool
    error: str = ""
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path.home() / ".local" / "state" / "ai-cli" / "gemini-logs"


def _log(msg: str, *, quiet: bool = False, verbose: bool = False, is_verbose: bool = False):
    """Print a log message to stderr."""
    if is_verbose and not verbose:
        return
    if not quiet:
        print(msg, file=sys.stderr)


def _log_to_file(result: GeminiResult, prompt: str, output_path: str | None):
    """Append a structured log entry to the log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": result.model,
        "tier": result.tier,
        "tier_name": result.tier_name,
        "success": result.success,
        "error": result.error or None,
        "duration_ms": result.duration_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
        "prompt_chars": len(prompt),
        "response_chars": len(result.content),
        "output_path": output_path,
        "attempts": [
            {
                "tier": a.tier,
                "tier_name": a.tier_name,
                "model": a.model,
                "success": a.success,
                "error": a.error or None,
                "duration_ms": a.duration_ms,
            }
            for a in result.attempts
        ],
    }

    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Tier 1: gemini CLI (OAuth)
# ---------------------------------------------------------------------------


def _try_gemini_cli(prompt: str, model: str, timeout_s: int, verbose: bool) -> GeminiResult:
    """Try running via gemini CLI (OAuth auth — free tier)."""
    gemini_bin = shutil.which("gemini")
    if not gemini_bin:
        return GeminiResult(
            model=model,
            tier=1,
            tier_name=TIER_NAMES[1],
            success=False,
            error="gemini CLI not found in PATH",
        )

    cmd = [gemini_bin, "-p", prompt, "-m", model, "--yolo"]

    if verbose:
        _log(f"  [tier 1] Running: gemini -p <prompt> -m {model} --yolo", verbose=True, is_verbose=True)

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        elapsed_ms = int((time.time() - start) * 1000)

        if result.returncode == 0 and result.stdout.strip():
            # Strip the YOLO mode and keychain warnings from output
            lines = result.stdout.split("\n")
            content_lines = []
            skip_patterns = [
                "YOLO mode is enabled",
                "Keychain initialization",
                "Using FileKeychain",
                "Loaded cached credentials",
            ]
            for line in lines:
                if not any(pat in line for pat in skip_patterns):
                    content_lines.append(line)
            content = "\n".join(content_lines).strip()

            return GeminiResult(
                content=content,
                model=model,
                tier=1,
                tier_name=TIER_NAMES[1],
                success=True,
                duration_ms=elapsed_ms,
            )

        # Check stderr for capacity/rate limit errors
        stderr = result.stderr or ""
        if "429" in stderr or "RESOURCE_EXHAUSTED" in stderr or "capacity" in stderr.lower():
            return GeminiResult(
                model=model,
                tier=1,
                tier_name=TIER_NAMES[1],
                success=False,
                error="capacity exhausted (429)",
                duration_ms=elapsed_ms,
            )

        return GeminiResult(
            model=model,
            tier=1,
            tier_name=TIER_NAMES[1],
            success=False,
            error=f"exit code {result.returncode}: {stderr[:200]}",
            duration_ms=elapsed_ms,
        )

    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.time() - start) * 1000)
        return GeminiResult(
            model=model,
            tier=1,
            tier_name=TIER_NAMES[1],
            success=False,
            error=f"timeout ({timeout_s}s)",
            duration_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Tiers 2 & 3: Gemini REST API (free / paid keys)
# ---------------------------------------------------------------------------


def _try_gemini_api(prompt: str, model: str, timeout_s: int, tier: int, verbose: bool) -> GeminiResult:
    """Try running via Gemini REST API with an API key."""
    tier_name = TIER_NAMES.get(tier, f"tier-{tier}")

    # Determine which env var to use
    if tier == 2:
        api_key = os.environ.get("GOOGLE_API_KEY_FREE_TIER")
        if not api_key:
            return GeminiResult(
                model=model,
                tier=tier,
                tier_name=tier_name,
                success=False,
                error="GOOGLE_API_KEY_FREE_TIER not set",
            )
    elif tier == 3:
        api_key = os.environ.get("GOOGLE_API_KEY_TIER_1") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return GeminiResult(
                model=model,
                tier=tier,
                tier_name=tier_name,
                success=False,
                error="GOOGLE_API_KEY_TIER_1 not set",
            )
    else:
        return GeminiResult(
            model=model,
            tier=tier,
            tier_name=tier_name,
            success=False,
            error=f"unknown tier {tier}",
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return GeminiResult(
            model=model,
            tier=tier,
            tier_name=tier_name,
            success=False,
            error="google-genai package not installed (pip install google-genai)",
        )

    # Resolve model alias for API
    # deep-think alias uses gemini-3.1-pro-preview with HIGH thinking
    base_model = model
    thinking_config = None

    if model == "deep-think":
        base_model = "gemini-3.1-pro-preview"
        thinking_config = types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH)
    elif model in MODEL_ALIASES:
        base_model = MODEL_ALIASES[model]

    config = types.GenerateContentConfig(
        thinking_config=thinking_config,
    )

    if verbose:
        _log(f"  [tier {tier}] Calling {base_model} via REST API ({tier_name})", verbose=True, is_verbose=True)

    client = genai.Client(api_key=api_key)
    start = time.time()

    # Thread-based timeout (SDK timeout can be buggy)
    result_container = {}
    error_container = {}

    def _call():
        try:
            result_container["response"] = client.models.generate_content(
                model=base_model,
                contents=prompt,
                config=config,
            )
        except Exception as e:
            error_container["error"] = e

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    elapsed_ms = int((time.time() - start) * 1000)

    if thread.is_alive():
        return GeminiResult(
            model=model,
            tier=tier,
            tier_name=tier_name,
            success=False,
            error=f"timeout ({timeout_s}s)",
            duration_ms=elapsed_ms,
        )

    if "error" in error_container:
        exc = error_container["error"]
        error_msg = str(exc)
        is_quota = "429" in error_msg or "resource exhausted" in error_msg.lower()
        return GeminiResult(
            model=model,
            tier=tier,
            tier_name=tier_name,
            success=False,
            error=f"{'capacity exhausted (429)' if is_quota else error_msg[:200]}",
            duration_ms=elapsed_ms,
        )

    if "response" not in result_container:
        return GeminiResult(
            model=model,
            tier=tier,
            tier_name=tier_name,
            success=False,
            error="no response received",
            duration_ms=elapsed_ms,
        )

    api_response = result_container["response"]
    content = getattr(api_response, "text", None) or ""

    # Extract usage metadata
    input_tokens = output_tokens = total_tokens = 0
    if hasattr(api_response, "usage_metadata") and api_response.usage_metadata:
        um = api_response.usage_metadata
        input_tokens = getattr(um, "prompt_token_count", 0) or 0
        output_tokens = getattr(um, "candidates_token_count", 0) or 0
        total_tokens = getattr(um, "total_token_count", 0) or 0

    return GeminiResult(
        content=content,
        model=model,
        tier=tier,
        tier_name=tier_name,
        success=True,
        duration_ms=elapsed_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


# ---------------------------------------------------------------------------
# Main: 3-tier fallback chain
# ---------------------------------------------------------------------------


def run_gemini(
    prompt: str,
    *,
    model: str = "deep-think",
    output: str | None = None,
    quiet: bool = False,
    verbose: bool = False,
    timeout_s: int = 600,
) -> GeminiResult:
    """Run a Gemini prompt with 3-tier auth fallback.

    Tries: gemini CLI (OAuth) → free API key → paid API key.
    On 429/capacity errors, automatically falls through to next tier.

    Returns GeminiResult with content and metadata.
    """
    _log(f"[ai gemini] model={model} prompt={len(prompt)} chars", quiet=quiet)

    final_result = GeminiResult(model=model)
    tiers = [
        (1, lambda: _try_gemini_cli(prompt, model, timeout_s, verbose)),
        (2, lambda: _try_gemini_api(prompt, model, timeout_s, 2, verbose)),
        (3, lambda: _try_gemini_api(prompt, model, timeout_s, 3, verbose)),
    ]

    for tier_num, tier_fn in tiers:
        tier_name = TIER_NAMES[tier_num]
        _log(f"  → trying {tier_name}...", quiet=quiet)

        result = tier_fn()
        attempt = AttemptLog(
            tier=tier_num,
            tier_name=tier_name,
            model=model,
            success=result.success,
            error=result.error,
            duration_ms=result.duration_ms,
        )
        final_result.attempts.append(attempt)

        if result.success:
            final_result.content = result.content
            final_result.tier = tier_num
            final_result.tier_name = tier_name
            final_result.success = True
            final_result.duration_ms = result.duration_ms
            final_result.input_tokens = result.input_tokens
            final_result.output_tokens = result.output_tokens
            final_result.total_tokens = result.total_tokens
            _log(f"  ✓ {tier_name} succeeded ({result.duration_ms}ms, {len(result.content)} chars)", quiet=quiet)
            break

        # Log failure and check if we should fall through
        is_capacity = "capacity" in result.error or "429" in result.error
        is_not_configured = "not set" in result.error or "not found" in result.error or "not installed" in result.error

        if is_capacity:
            _log(f"  ✗ {tier_name}: capacity exhausted — falling through", quiet=quiet)
        elif is_not_configured:
            _log(f"  ✗ {tier_name}: not configured — skipping", quiet=quiet, verbose=True, is_verbose=True)
        else:
            # Non-capacity error — still fall through but log it
            _log(f"  ✗ {tier_name}: {result.error[:100]}", quiet=quiet)

    if not final_result.success:
        final_result.error = "all tiers failed"
        _log("  ✗ all tiers exhausted", quiet=quiet)

    # Write output
    output_path = output
    if output_path is None and final_result.success:
        # Auto-generate output path
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        output_path = str(DEFAULT_OUTPUT_DIR / f"{ts}-{model}.md")

    if output_path and final_result.success:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(final_result.content)
        _log(f"  → output written to {output_path}", quiet=quiet)

    # Write to stdout unless quiet
    if not quiet and final_result.success:
        print(final_result.content)

    # Log to file
    _log_to_file(final_result, prompt, output_path)

    return final_result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def gemini_cli(args: list[str]):
    """Parse args and run the Gemini fallback chain.

    Usage:
      ai gemini "prompt text" -m deep-think
      ai gemini "prompt text" -m pro -o output.md
      ai gemini "prompt text" -m flash --quiet
      cat prompt.txt | ai gemini -m deep-think -o output.md
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="ai gemini",
        description="Run a Gemini prompt with 3-tier auth fallback",
    )
    parser.add_argument("prompt", nargs="?", default=None, help="Prompt text (or pipe via stdin)")
    parser.add_argument(
        "-m", "--model", default="deep-think", help="Model alias: deep-think, pro, flash, flash-lite, or full model ID"
    )
    parser.add_argument("-o", "--output", default=None, help="Output file path (auto-generated if not specified)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress stdout output (file only)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed tier/model info")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds (default: 600)")
    parser.add_argument("--no-file", action="store_true", help="Don't write output to file, stdout only")

    parsed = parser.parse_args(args)

    # Get prompt from arg or stdin
    prompt = parsed.prompt
    if prompt is None:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read()
        else:
            parser.error("no prompt provided — pass as argument or pipe via stdin")

    if not prompt or not prompt.strip():
        parser.error("prompt is empty")

    output = parsed.output
    if parsed.no_file:
        output = "/dev/null"  # effectively discard

    result = run_gemini(
        prompt,
        model=parsed.model,
        output=output,
        quiet=parsed.quiet,
        verbose=parsed.verbose,
        timeout_s=parsed.timeout,
    )

    sys.exit(0 if result.success else 1)
