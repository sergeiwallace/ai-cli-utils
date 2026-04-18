"""Gemini research wrapper with auth-tier fallback chain.

Fallback chain:
  oauth:          gemini CLI (OAuth — free, Google AI subscription)
  ai_studio_free: Gemini REST API with free-tier key (GOOGLE_API_KEY_FREE_TIER)
  ai_studio_paid: Gemini REST API with paid key (GOOGLE_API_KEY_TIER_1)

The paid tier is disabled by default (paid_fallback_enabled = false in config).
Set paid_fallback_enabled = true to enable deep-research (AI Studio Interactions API).

Usage:
  ai gemini "prompt" -m deep-think
  ai gemini "prompt" -m gemini-3.1-pro-preview -o output.md
  ai gemini "prompt" -m gemini-3-flash-preview --quiet
  cat prompt.txt | ai gemini -m deep-think -o output.md
  ai gemini "prompt" -m deep-research   # requires paid_fallback_enabled=true in config
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

# Model prefixes that have free quota under GOOGLE_API_KEY_FREE_TIER.
# Source: https://ai.google.dev/gemini-api/docs/pricing
#
# Free tier includes: Flash text/multimodal models (2.0, 2.5, 3.x), Gemma 4,
# Gemini Embedding.
# NOT free: Pro models (2.5 Pro, 3.1 Pro), image-generation variants
# (gemini-3.1-flash-image-preview, gemini-3-pro-image-preview), and all
# video/image/music generation models (Veo, Imagen, Lyria).
#
# "gemini-3.1-flash" is intentionally absent — it would match both
# gemini-3.1-flash-live-preview (free) AND gemini-3.1-flash-image-preview
# (paid). Use specific prefixes for each free sub-variant instead.
_FREE_TIER_MODEL_PREFIXES: frozenset[str] = frozenset(
    [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-3-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-live",
        "gemma-4",
        "gemini-embedding",
    ]
)


__all__ = ["GeminiResult", "AttemptLog", "run_gemini"]


def _is_free_tier_eligible(model: str) -> bool:
    """Return True if this model has a free quota tier under GOOGLE_API_KEY_FREE_TIER.

    Flash text/multimodal models (including gemini-3.1-flash-live-preview),
    Gemma 4, and Gemini Embedding are free-tier eligible. Pro models
    (deep-think, pro, gemini-3.1-pro-*), image-generation Flash variants
    (gemini-3.1-flash-image-preview, gemini-3-pro-image-preview), and
    deep-research are not — the free-tier key returns a billing error for them
    rather than a 429.
    """
    if model == "deep-think":
        return False
    resolved = MODEL_ALIASES.get(model, model)
    return any(resolved.startswith(prefix) for prefix in _FREE_TIER_MODEL_PREFIXES)


# Tier names for logging — Google-aligned names (T-01)
TIER_NAMES = {
    1: "oauth",
    2: "ai_studio_free",
    3: "ai_studio_paid",
}

# Deep Research daily run tracking — budget alert thresholds only.
# There is no Google-imposed hard daily limit on paid Interactions API runs.
# These constants are soft budget alerts to flag unexpectedly high paid usage.
DEEP_RESEARCH_DAILY_LIMIT: int = 20  # alert threshold: warn above this many paid runs/day
DEEP_RESEARCH_DAILY_WARNING: int = 18  # warn when paid count reaches this

DR_DAILY_FILE = Path.home() / ".local" / "state" / "ai-cli" / "dr-daily.json"

# Default output directory for auto-generated output files
DEFAULT_OUTPUT_DIR = Path.home() / ".local" / "state" / "ai-cli" / "gemini-output"

# Paths treated as "no output file" -- suppress file write
_NULL_OUTPUT = {"/dev/null", os.devnull}


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
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    is_deep_research: bool = False
    attempts: list = field(default_factory=list)
    event_id: str = ""
    machine: str = ""


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


def _publish_gemini_event_nats(entry: dict) -> None:
    """Fire-and-forget NATS publish of a Gemini usage event.

    Published to hw.events.usage.gemini.event so the humanware UsageConsumer
    can ingest it into Postgres. Runs in a daemon thread — never blocks callers.
    Silently no-ops if NATS is unavailable or not configured.
    """
    import asyncio
    import threading as _threading

    from .messaging import NATSClient

    try:
        from .main import load_config as _load_config

        cfg = _load_config()
        nats_servers = cfg.get("messaging", {}).get("nats_servers")
    except Exception:
        nats_servers = None

    if not nats_servers:
        return

    async def _do() -> None:
        client = NATSClient(servers=nats_servers)
        try:
            await client.connect()
            if client.nc:
                await client.publish("hw.events.usage.gemini.event", entry)
        finally:
            await client.close()

    def _run() -> None:
        try:
            asyncio.run(_do())
        except Exception:
            pass

    _threading.Thread(target=_run, daemon=True).start()


def _log_to_file(result: GeminiResult, prompt: str, output_path: str | None) -> None:
    """Append a structured log entry to the log file."""
    import uuid as _uuid

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"

    event_id = str(_uuid.uuid4())
    machine = os.environ.get("AI_CLI_HOST", "")
    occurred_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    source_quality = "suspected_test" if len(prompt) < 20 else "ok"

    entry = {
        "id": event_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "occurred_at": occurred_at,
        "machine": machine,
        "provider": "gemini",
        "model": result.model,
        "tier": result.tier,
        "tier_name": result.tier_name,
        "success": result.success,
        "error": result.error or None,
        "duration_ms": result.duration_ms,
        "is_deep_research": result.is_deep_research,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
        "prompt_chars": len(prompt),
        "response_chars": len(result.content),
        "output_path": output_path,
        "source_quality": source_quality,
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

    _publish_gemini_event_nats(entry)


# ---------------------------------------------------------------------------
# Secrets loading
# ---------------------------------------------------------------------------

_DOPPLER_KEYS = ["GOOGLE_API_KEY_FREE_TIER", "GOOGLE_API_KEY_TIER_1", "GEMINI_API_KEY"]


def _load_doppler_secrets() -> None:
    """Inject missing API keys from Doppler into os.environ.

    Only runs if at least one key is absent — skips entirely if all are set.
    Silently no-ops if doppler is not installed or the project is not configured.
    """
    if all(os.environ.get(k) for k in _DOPPLER_KEYS[:2]):
        return  # both keys already present — nothing to do

    doppler_bin = shutil.which("doppler")
    if not doppler_bin:
        return

    try:
        _doppler_project = os.environ.get("AI_CLI_DOPPLER_PROJECT", "")
        _doppler_cmd = [doppler_bin, "run"]
        if _doppler_project:
            _doppler_cmd += ["--project", _doppler_project]
        _doppler_cmd += ["--config", "dev", "--", "env"]
        result = subprocess.run(
            _doppler_cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key in _DOPPLER_KEYS and not os.environ.get(key):
                os.environ[key] = value
    except Exception:
        pass  # non-fatal — fallback tiers will report their own errors


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

    # Extract usage metadata — log None (not 0) when unavailable so missing
    # data is distinguishable from a model that genuinely returned zero tokens.
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    if hasattr(api_response, "usage_metadata") and api_response.usage_metadata:
        um = api_response.usage_metadata
        pt = getattr(um, "prompt_token_count", None)
        ct = getattr(um, "candidates_token_count", None)
        tt = getattr(um, "total_token_count", None)
        input_tokens = int(pt) if pt is not None else None
        output_tokens = int(ct) if ct is not None else None
        total_tokens = int(tt) if tt is not None else None

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
# Config helpers
# ---------------------------------------------------------------------------


def _load_paid_fallback_config() -> bool:
    """Return paid_fallback_enabled from config (default: False).

    When False, the ai_studio_paid tier is excluded from the fallback chain
    for all models, and Deep Research exits with an actionable error.
    """
    try:
        from .main import load_config

        return bool(load_config().get("gemini", {}).get("paid_fallback_enabled", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Deep Research daily counter
# ---------------------------------------------------------------------------


def _read_dr_daily() -> dict:
    """Read today's Deep Research run counter from disk.

    Returns a dict with keys: date, oauth_count, paid_count, last_run.
    Returns a zeroed dict if the file is absent or dated to a different day.
    """
    today = time.strftime("%Y-%m-%d")
    if DR_DAILY_FILE.exists():
        try:
            data = json.loads(DR_DAILY_FILE.read_text())
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "oauth_count": 0, "paid_count": 0, "last_run": None}


def _increment_dr_counter(tier_name: str) -> dict:
    """Increment the daily Deep Research run counter and persist it.

    tier_name should be "oauth" or "ai_studio_paid".
    Returns the updated counter dict.
    """
    data = _read_dr_daily()
    if tier_name == TIER_NAMES[1]:  # "oauth"
        data["oauth_count"] = data.get("oauth_count", 0) + 1
    else:
        data["paid_count"] = data.get("paid_count", 0) + 1
    data["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    DR_DAILY_FILE.parent.mkdir(parents=True, exist_ok=True)
    DR_DAILY_FILE.write_text(json.dumps(data))
    return data


# ---------------------------------------------------------------------------
# Deep Research — Interactions API
# ---------------------------------------------------------------------------

_DEEP_RESEARCH_AGENT = "deep-research-pro-preview-12-2025"
_INTERACTIONS_BASE = "https://generativelanguage.googleapis.com/v1beta/interactions"
_DEEP_RESEARCH_POLL_INTERVAL = 30  # seconds between status polls


def _run_deep_research(
    prompt: str,
    *,
    output: str | None = None,
    quiet: bool = False,
    timeout_s: int = 3600,
    paid_fallback_enabled: bool = False,
) -> GeminiResult:
    """Submit a Deep Research job via the Interactions API and poll until complete.

    Auth: GOOGLE_API_KEY_TIER_1 (ai_studio_paid). Requires paid_fallback_enabled=True
    in config. Cancels via DELETE on KeyboardInterrupt.
    """
    import urllib.error
    import urllib.request

    _load_doppler_secrets()

    _log("[ai gemini] model=deep-research (Interactions API)", quiet=quiet)

    # Gate: paid tier must be enabled in config
    if not paid_fallback_enabled:
        _log(
            "[ai gemini] No available auth method succeeded.\n"
            "  AI Studio paid fallback is disabled (paid_fallback_enabled = false in config).\n"
            "  To enable: set paid_fallback_enabled = true in ~/.config/ai-cli/config.toml",
            quiet=quiet,
        )
        return GeminiResult(
            model="deep-research",
            is_deep_research=True,
            success=False,
            error="AI Studio paid fallback is disabled (paid_fallback_enabled = false in config).",
        )

    api_key = os.environ.get("GOOGLE_API_KEY_TIER_1") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return GeminiResult(
            model="deep-research",
            is_deep_research=True,
            success=False,
            error="GOOGLE_API_KEY_TIER_1 not set",
        )
    _log("  → using paid API key (ai_studio_paid)", quiet=quiet)
    _auth_header: dict[str, str] = {}
    _auth_param = f"?key={api_key}"

    # Submit
    payload = json.dumps(
        {
            "agent": _DEEP_RESEARCH_AGENT,
            "input": prompt,
            "background": True,
            "store": True,
        }
    ).encode()

    submit_url = f"{_INTERACTIONS_BASE}{_auth_param}"
    req = urllib.request.Request(
        submit_url,
        data=payload,
        headers={"Content-Type": "application/json", **_auth_header},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            interaction = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return GeminiResult(
            model="deep-research",
            success=False,
            error=f"submit failed: HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}",
        )
    except Exception as exc:
        return GeminiResult(model="deep-research", success=False, error=f"submit failed: {exc}")

    # API returns "id" (flat string) not "name" (resource path)
    interaction_id = interaction.get("id") or interaction.get("name", "").split("/")[-1]
    if not interaction_id:
        return GeminiResult(
            model="deep-research",
            success=False,
            error=f"no interaction ID in submit response: {interaction}",
        )

    _log(f"  → submitted (id={interaction_id}), polling every {_DEEP_RESEARCH_POLL_INTERVAL}s...", quiet=quiet)

    poll_url = f"{_INTERACTIONS_BASE}/{interaction_id}{_auth_param}"
    start = time.time()
    status_data: dict = {}

    def _cancel() -> None:
        try:
            cancel_req = urllib.request.Request(poll_url, method="DELETE", headers=_auth_header)
            urllib.request.urlopen(cancel_req, timeout=10)
        except Exception:
            pass

    try:
        while True:
            elapsed = int(time.time() - start)
            if elapsed > timeout_s:
                _cancel()
                return GeminiResult(
                    model="deep-research",
                    success=False,
                    error=f"timed out after {elapsed}s",
                )

            mins, secs = divmod(elapsed, 60)
            _log(f"  ⏳ deep research running... ({mins}m {secs}s elapsed)", quiet=quiet)
            time.sleep(_DEEP_RESEARCH_POLL_INTERVAL)

            try:
                poll_req = urllib.request.Request(poll_url, method="GET", headers=_auth_header)
                with urllib.request.urlopen(poll_req, timeout=30) as resp:
                    status_data = json.loads(resp.read())
            except Exception as exc:
                _log(f"  ✗ poll error: {exc} — retrying", quiet=quiet)
                continue

            state = (status_data.get("state") or status_data.get("status") or "").lower()
            if state == "completed":
                break
            if state in {"failed", "cancelled", "error"}:
                return GeminiResult(
                    model="deep-research",
                    success=False,
                    error=f"interaction {state}: {status_data}",
                )

    except KeyboardInterrupt:
        _log("\n  → cancelling deep research run...", quiet=False)
        _cancel()
        return GeminiResult(model="deep-research", success=False, error="cancelled by user")

    # Extract output text
    outputs = status_data.get("outputs", [])
    content = outputs[-1].get("text", "") if outputs else ""
    if not content:
        return GeminiResult(
            model="deep-research",
            success=False,
            error=f"no output text in completed response: {status_data}",
        )

    elapsed_total = int(time.time() - start)
    mins, secs = divmod(elapsed_total, 60)
    _log(f"  ✓ deep research complete ({mins}m {secs}s, {len(content)} chars)", quiet=quiet)

    result = GeminiResult(
        content=content,
        model="deep-research",
        tier=3,
        tier_name=TIER_NAMES[3],  # "ai_studio_paid"
        is_deep_research=True,
        success=True,
        duration_ms=elapsed_total * 1000,
    )

    # Write output (mirrors run_gemini output handling)
    output_path = output
    if output_path is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        output_path = str(DEFAULT_OUTPUT_DIR / f"{ts}-deep-research.md")

    if output_path and output_path not in _NULL_OUTPUT:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(content)
        _log(f"  → output written to {output_path}", quiet=quiet)

    if not quiet:
        print(content)

    _log_to_file(result, prompt, output_path)

    # Increment counter after successful completion only
    counter = _increment_dr_counter(TIER_NAMES[3])
    if not quiet:
        paid_today = counter.get("paid_count", 0)
        print(
            f"[deep-research] Paid (AI Studio) runs today: {paid_today} — check `ai spend gemini` for charges",
            file=sys.stderr,
        )
        if paid_today >= DEEP_RESEARCH_DAILY_WARNING:
            print(
                f"[deep-research] Warning: budget alert — {paid_today} paid runs today"
                f" (threshold: {DEEP_RESEARCH_DAILY_LIMIT}). Check `ai spend gemini`.",
                file=sys.stderr,
            )

    return result


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
    start_tier: int = 1,
    paid_fallback_enabled: bool | None = None,
) -> GeminiResult:
    """Run a Gemini prompt with auth-tier fallback chain.

    Tries: oauth (gemini CLI) → ai_studio_free (free API key) → ai_studio_paid (paid key).
    The paid tier requires paid_fallback_enabled=True (config) to be included.
    On 429/capacity errors, automatically falls through to the next available tier.

    start_tier skips earlier tiers entirely (e.g. start_tier=2 bypasses OAuth).
    paid_fallback_enabled: override config value; None means read from config.

    Returns GeminiResult with content and metadata.
    """
    if paid_fallback_enabled is None:
        paid_fallback_enabled = _load_paid_fallback_config()

    if model == "deep-research":
        return _run_deep_research(
            prompt,
            output=output,
            quiet=quiet,
            timeout_s=timeout_s,
            paid_fallback_enabled=paid_fallback_enabled,
        )

    _load_doppler_secrets()
    _log(f"[ai gemini] model={model} prompt={len(prompt)} chars", quiet=quiet)

    final_result = GeminiResult(model=model)
    tiers = [
        (1, lambda: _try_gemini_cli(prompt, model, timeout_s, verbose)),
        (2, lambda: _try_gemini_api(prompt, model, timeout_s, 2, verbose)),
        (3, lambda: _try_gemini_api(prompt, model, timeout_s, 3, verbose)),
    ]
    tiers = [(n, fn) for n, fn in tiers if n >= start_tier]

    # Remove paid tier when disabled in config
    _paid_filtered = not paid_fallback_enabled
    if _paid_filtered:
        tiers = [(n, fn) for n, fn in tiers if n != 3]

    # Skip tier 2 for models with no free quota — avoids a wasted round-trip that
    # returns a billing error rather than a 429.
    if any(n == 2 for n, _ in tiers) and not _is_free_tier_eligible(model):
        tiers = [(n, fn) for n, fn in tiers if n != 2]
        _log(
            f"  → skipping {TIER_NAMES[2]}: {model} has no free quota tier",
            quiet=quiet,
        )

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
        if _paid_filtered:
            _log(
                "  AI Studio paid fallback is disabled (paid_fallback_enabled = false in config).\n"
                "  To enable: set paid_fallback_enabled = true in ~/.config/ai-cli/config.toml\n"
                "  (Do this only after confirming billing credit status — see AI-CLI-43)",
                quiet=quiet,
            )

    # Write output
    output_path = output
    if output_path is None and final_result.success:
        # Auto-generate output path
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        output_path = str(DEFAULT_OUTPUT_DIR / f"{ts}-{model}.md")

    if output_path and final_result.success and output_path not in _NULL_OUTPUT:
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
      ai gemini "prompt text" -m flash -q
      cat prompt.txt | ai gemini -m deep-think -o output.md
      ai gemini "prompt" -m flash -F           # stdout only, no file
      ai gemini "prompt" -m flash -t 120       # 120s timeout
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="ai gemini",
        description="Run a Gemini prompt with 3-tier auth fallback",
    )
    parser.add_argument("prompt", nargs="?", default=None, help="Prompt text (or pipe via stdin)")
    parser.add_argument(
        "-m",
        "--model",
        default="deep-think",
        help="Model alias: deep-think, pro, flash, flash-lite, deep-research, or full model ID",
    )
    parser.add_argument("-o", "--output", default=None, help="Output file path (auto-generated if not specified)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress stdout output (file only)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed tier/model info")
    parser.add_argument("-t", "--timeout", type=int, default=600, help="Timeout in seconds (default: 600)")
    parser.add_argument("-F", "--no-file", action="store_true", help="Don't write output to file, stdout only")
    parser.add_argument(
        "-d",
        "--depth",
        choices=["quick", "standard"],
        default="quick",
        help="Research depth: quick (default, single-shot) or standard (Planner-Executor with grounded search)",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="RUN_ID",
        help="Resume a standard/deep run from last completed step",
    )
    parser.add_argument(
        "--planning-model",
        default=None,
        metavar="MODEL",
        help="Override planning model for standard/deep depth (default: deep-think)",
    )
    parser.add_argument(
        "-s",
        "--start-tier",
        type=int,
        choices=[1, 2, 3],
        default=1,
        metavar="TIER",
        help="Start at this auth tier (1=OAuth CLI, 2=free API key, 3=paid API key; default: 1)",
    )
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

    depth = parsed.depth
    if parsed.resume and depth == "quick":
        depth = "standard"  # resume always implies standard/deep

    if depth == "quick":
        result = run_gemini(
            prompt,
            model=parsed.model,
            output=output,
            quiet=parsed.quiet,
            verbose=parsed.verbose,
            timeout_s=parsed.timeout,
            start_tier=parsed.start_tier,
        )
    else:
        from .research import load_depth_preset, run_standard

        preset = load_depth_preset(depth, planning_model_override=parsed.planning_model)
        result = run_standard(
            prompt,
            preset,
            resume=parsed.resume,
            output=output,
            quiet=parsed.quiet,
            verbose=parsed.verbose,
            timeout_s=parsed.timeout,
        )

    if not result.success and result.error:
        _log(f"  ✗ {result.error}", quiet=parsed.quiet)
    sys.exit(0 if result.success else 1)
