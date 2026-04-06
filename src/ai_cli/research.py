"""Research depth orchestration for ai gemini --depth standard|deep.

Implements the Planner-Executor (standard) tier:
  1. Query generation -- planning model generates structured search queries
  2. Concurrent grounded search -- Gemini native grounding via REST API
  3. Synthesis -- synthesis model combines results into a final answer

Per-step checkpointing at:
  ~/.local/state/ai-cli/research-runs/<run-id>/
"""

import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RESEARCH_RUNS_DIR = Path.home() / ".local" / "state" / "ai-cli" / "research-runs"
RESEARCH_CONFIG_PATH = Path.home() / ".config" / "ai-cli" / "research.yaml"

_DEFAULT_PRESETS_YAML = """
depth_presets:
  quick:
    model: deep-think
    search_provider: null
    search_rounds: 0
    planning_model: null
    reflection_rounds: 0

  standard:
    model: deep-think
    search_provider: gemini
    search_rounds: 1
    planning_model: deep-think
    reflection_rounds: 0
    max_search_queries: 5
    max_concurrent_searches: 3
    retries:
      cli_timeout_retries: 1
      step_node_retries: 2

  deep:
    model: deep-think
    search_provider: gemini
    search_rounds: 2
    planning_model: deep-think
    reflection_rounds: 1
    max_search_queries: 5
    max_concurrent_searches: 3
    retries:
      cli_timeout_retries: 1
      step_node_retries: 2
"""


@dataclass
class DepthPreset:
    model: str = "deep-think"
    search_provider: str | None = None
    search_rounds: int = 0
    planning_model: str | None = None
    reflection_rounds: int = 0
    max_search_queries: int = 5
    max_concurrent_searches: int = 3
    retries: dict = field(default_factory=lambda: {"cli_timeout_retries": 1, "step_node_retries": 2})


def load_depth_preset(depth: str, *, planning_model_override: str | None = None) -> DepthPreset:
    """Load depth preset from user config, falling back to built-in defaults."""
    import yaml

    raw: dict[str, Any] = {}

    if RESEARCH_CONFIG_PATH.exists():
        try:
            with open(RESEARCH_CONFIG_PATH) as f:
                user_config = yaml.safe_load(f) or {}
            raw = (user_config.get("depth_presets") or {}).get(depth, {})
        except Exception:
            pass

    if not raw:
        defaults = yaml.safe_load(_DEFAULT_PRESETS_YAML)
        raw = (defaults.get("depth_presets") or {}).get(depth, {})

    if not raw:
        raise ValueError(f"Unknown depth preset: {depth!r}")

    preset = DepthPreset(
        model=raw.get("model", "deep-think"),
        search_provider=raw.get("search_provider"),
        search_rounds=raw.get("search_rounds", 0),
        planning_model=raw.get("planning_model"),
        reflection_rounds=raw.get("reflection_rounds", 0),
        max_search_queries=raw.get("max_search_queries", 5),
        max_concurrent_searches=raw.get("max_concurrent_searches", 3),
        retries=raw.get("retries", {"cli_timeout_retries": 1, "step_node_retries": 2}),
    )

    if planning_model_override:
        preset.planning_model = planning_model_override

    return preset


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


def make_run_id() -> str:
    """Generate a unique run ID: YYYYMMDD-HHMMSS-<6hex>."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{ts}-{suffix}"


def checkpoint_path(run_id: str, step_name: str) -> Path:
    return RESEARCH_RUNS_DIR / run_id / f"{step_name}.json"


def save_checkpoint(run_id: str, step_name: str, data: dict) -> None:
    path = checkpoint_path(run_id, step_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**data, "completed": True, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_checkpoint(run_id: str, step_name: str) -> dict | None:
    """Return checkpoint data if the step completed, else None."""
    path = checkpoint_path(run_id, step_name)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if data.get("completed") else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Grounded search
# ---------------------------------------------------------------------------


def _run_grounded_search(
    query: str,
    *,
    timeout_s: int = 120,
) -> dict:
    """Run one grounded search via Gemini REST API with native Google Search grounding.

    Tries free-tier key first, then paid tier. Returns:
      {"query": str, "content": str, "success": bool, "error": str}
    """
    from ai_cli.gemini import _load_doppler_secrets

    _load_doppler_secrets()

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {
            "query": query,
            "content": "",
            "success": False,
            "error": "google-genai not installed",
        }

    search_model = "gemini-2.0-flash"

    for env_var in ("GOOGLE_API_KEY_FREE_TIER", "GOOGLE_API_KEY_TIER_1"):
        api_key = os.environ.get(env_var)
        if not api_key:
            continue

        result_holder: dict = {}
        error_holder: dict = {}

        def _call(
            _client=genai.Client(api_key=api_key),
            _model=search_model,
            _query=query,
            _rh=result_holder,
            _eh=error_holder,
        ) -> None:
            try:
                tool = types.Tool(google_search=types.GoogleSearch())
                config = types.GenerateContentConfig(tools=[tool])
                response = _client.models.generate_content(model=_model, contents=_query, config=config)
                _rh["response"] = response
            except Exception as exc:
                _eh["error"] = str(exc)

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=timeout_s)

        if t.is_alive():
            continue

        if "error" in error_holder:
            err = error_holder["error"]
            if "429" in err or "exhausted" in err.lower() or "quota" in err.lower():
                continue
            return {"query": query, "content": "", "success": False, "error": err}

        if "response" not in result_holder:
            continue

        content = getattr(result_holder["response"], "text", None) or ""
        if not content.strip():
            continue

        return {"query": query, "content": content, "success": True, "error": ""}

    return {
        "query": query,
        "content": "",
        "success": False,
        "error": "all API key tiers exhausted or not configured",
    }


def run_concurrent_searches(
    queries: list[str],
    *,
    max_concurrent: int = 3,
    timeout_s: int = 120,
    quiet: bool = False,
) -> list[dict]:
    """Run grounded searches for all queries concurrently."""
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(_run_grounded_search, q, timeout_s=timeout_s): q for q in queries}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            if not quiet:
                status = "ok" if result["success"] else "FAIL"
                label = result["query"][:60]
                print(f"  [{status}] search: {label}", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

_QUERY_GEN_PROMPT = """\
You are a research query generator. Given the following research prompt, generate {n} specific, \
targeted search queries that would help gather comprehensive, up-to-date information to answer it.

Each query should be self-contained and independently searchable, and cover a different aspect \
of the topic.

Respond with ONLY a JSON object -- no markdown, no explanation, no code fences:
{{"queries": ["query 1", "query 2", "query 3"]}}

Research prompt:
{prompt}\
"""


def generate_queries(
    prompt: str,
    *,
    planning_model: str,
    n: int,
    quiet: bool = False,
    verbose: bool = False,
    timeout_s: int = 300,
) -> list[str]:
    """Call the planning model to generate search queries, returned as a list of strings."""
    from ai_cli.gemini import run_gemini

    gen_prompt = _QUERY_GEN_PROMPT.format(n=n, prompt=prompt)

    if not quiet:
        print(f"  -> generating {n} search queries with {planning_model}...", file=sys.stderr)

    result = run_gemini(
        gen_prompt,
        model=planning_model,
        output=os.devnull,
        quiet=True,
        verbose=verbose,
        timeout_s=timeout_s,
    )

    if not result.success:
        raise RuntimeError(f"Query generation failed: {result.error}")

    content = result.content.strip()

    # Strip markdown code fences if the model wrapped the JSON anyway
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        parsed = json.loads(content)
        queries = parsed.get("queries", [])
    except json.JSONDecodeError as exc:
        # Try to extract JSON from within a larger response
        match = re.search(r'\{"queries":\s*\[.*?\]\}', content, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            queries = parsed.get("queries", [])
        else:
            raise ValueError(f"Could not parse query generation response as JSON: {exc}\nContent: {content[:200]}")

    if not queries:
        raise ValueError("Query generation returned an empty query list")

    return queries[:n]


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

_SYNTHESIS_PROMPT = """\
You are synthesizing research findings. Based on the search results below, provide a \
comprehensive, well-structured answer to the original question.

Original question:
{prompt}

Search results ({n_results} queries, {n_success} successful):
{results}

Instructions:
- Synthesize information across all search results
- Cite specific findings where relevant
- Structure your response clearly with headers
- Identify any gaps or contradictions in the sources
- Provide a clear, actionable conclusion\
"""


def synthesize(
    prompt: str,
    search_results: list[dict],
    *,
    synthesis_model: str,
    quiet: bool = False,
    verbose: bool = False,
    timeout_s: int = 600,
) -> "GeminiResult":  # noqa: F821
    """Call the synthesis model with the original prompt + all search results."""
    from ai_cli.gemini import GeminiResult, run_gemini

    n_results = len(search_results)
    n_success = sum(1 for r in search_results if r.get("success"))

    if n_success == 0:
        return GeminiResult(
            success=False,
            model=synthesis_model,
            error="No successful search results to synthesize",
        )

    parts = []
    for i, r in enumerate(search_results, 1):
        if r.get("success") and r.get("content"):
            parts.append(f"### Result {i}: {r['query']}\n\n{r['content']}")

    synthesis_prompt = _SYNTHESIS_PROMPT.format(
        prompt=prompt,
        n_results=n_results,
        n_success=n_success,
        results="\n\n---\n\n".join(parts),
    )

    if not quiet:
        print(f"  -> synthesizing with {synthesis_model}...", file=sys.stderr)

    return run_gemini(
        synthesis_prompt,
        model=synthesis_model,
        output=os.devnull,
        quiet=True,
        verbose=verbose,
        timeout_s=timeout_s,
    )


# ---------------------------------------------------------------------------
# Standard depth orchestration
# ---------------------------------------------------------------------------


def run_standard(
    prompt: str,
    preset: DepthPreset,
    *,
    resume: str | None = None,
    output: str | None = None,
    quiet: bool = False,
    verbose: bool = False,
    timeout_s: int = 600,
) -> "GeminiResult":  # noqa: F821
    """Planner-Executor: query generation -> concurrent search -> synthesis.

    Args:
        prompt: The original research question.
        preset: Depth configuration (loaded from research.yaml or built-in defaults).
        resume: Run ID to resume from last completed step. If None, starts fresh.
        output: Output file path. Auto-generated if None.
        quiet: Suppress stderr progress output.
        verbose: Show detailed tier/model info.
        timeout_s: Per-call timeout in seconds.
    """
    from ai_cli.gemini import DEFAULT_OUTPUT_DIR, GeminiResult, _log_to_file

    run_id = resume or make_run_id()
    is_resume = resume is not None

    if not quiet:
        action = f"resuming run {run_id}" if is_resume else f"starting run {run_id}"
        print(f"[ai gemini] {action} (standard depth)", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 1: Query generation
    # ------------------------------------------------------------------
    step1 = load_checkpoint(run_id, "step_01_query_generation") if is_resume else None
    if step1:
        queries = step1["queries"]
        if not quiet:
            print(f"  -> [resume] step 1: {len(queries)} queries", file=sys.stderr)
    else:
        planning_model = preset.planning_model or "deep-think"
        n_queries = min(preset.max_search_queries, 5)
        queries = generate_queries(
            prompt,
            planning_model=planning_model,
            n=n_queries,
            quiet=quiet,
            verbose=verbose,
            timeout_s=min(timeout_s, 300),
        )
        save_checkpoint(run_id, "step_01_query_generation", {"queries": queries})
        if not quiet:
            print(f"  [ok] step 1: {len(queries)} queries generated", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 2: Concurrent grounded searches
    # ------------------------------------------------------------------
    step2 = load_checkpoint(run_id, "step_02_search_results") if is_resume else None
    if step2:
        search_results = step2["results"]
        if not quiet:
            n_ok = sum(1 for r in search_results if r.get("success"))
            print(f"  -> [resume] step 2: {n_ok}/{len(search_results)} searches", file=sys.stderr)
    else:
        search_results = run_concurrent_searches(
            queries,
            max_concurrent=preset.max_concurrent_searches,
            timeout_s=min(timeout_s, 120),
            quiet=quiet,
        )
        save_checkpoint(run_id, "step_02_search_results", {"results": search_results})
        n_ok = sum(1 for r in search_results if r.get("success"))
        if not quiet:
            print(f"  [ok] step 2: {n_ok}/{len(search_results)} searches completed", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 3: Synthesis
    # ------------------------------------------------------------------
    step3 = load_checkpoint(run_id, "step_03_synthesis") if is_resume else None
    if step3:
        if not quiet:
            print("  -> [resume] step 3: synthesis loaded from checkpoint", file=sys.stderr)
        synthesis_result = GeminiResult(
            content=step3["content"],
            model=preset.model,
            success=True,
        )
    else:
        synthesis_result = synthesize(
            prompt,
            search_results,
            synthesis_model=preset.model,
            quiet=quiet,
            verbose=verbose,
            timeout_s=timeout_s,
        )
        if synthesis_result.success:
            save_checkpoint(run_id, "step_03_synthesis", {"content": synthesis_result.content})
            if not quiet:
                print(
                    f"  [ok] step 3: synthesis complete ({len(synthesis_result.content)} chars)",
                    file=sys.stderr,
                )
        else:
            if not quiet:
                print(f"  [FAIL] step 3: synthesis failed: {synthesis_result.error}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    output_path = output
    if output_path is None and synthesis_result.success:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        output_path = str(DEFAULT_OUTPUT_DIR / f"{ts}-standard.md")

    if output_path and synthesis_result.success and output_path not in _null_outputs():
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(synthesis_result.content)
        if not quiet:
            print(f"  -> output written to {output_path}", file=sys.stderr)

    if not quiet and synthesis_result.success:
        print(synthesis_result.content)

    _log_to_file(synthesis_result, prompt, output_path)
    return synthesis_result


def _null_outputs() -> set[str]:
    """Paths treated as 'no output file' -- suppress file write."""
    return {"/dev/null", os.devnull}
