---
title: "Citation Validation for ai gemini Research Output"
category: design
tags: [citation-validation, gemini, research, lychee, semantic-scholar, AI-CLI-50]
status: draft
source: claude-sonnet-4-6 2026-04-21
---

# Citation Validation for `ai gemini` Research Output

## Table of Contents

- [Problem](#problem)
- [Scope](#scope)
- [Prior Art](#prior-art)
- [Design Decisions](#design-decisions)
  - [D1: Validation scope — arXiv only vs web URLs vs both](#d1-validation-scope)
  - [D2: Where validation code lives](#d2-where-code-lives)
  - [D3: Optional dependencies strategy](#d3-optional-dependencies-strategy)
  - [D4: Pipeline integration — auto vs opt-in](#d4-pipeline-integration)
  - [D5: Blocking behavior](#d5-blocking-behavior)
  - [D6: Output format](#d6-output-format)
- [Architecture](#architecture)
- [Validation Pipeline Steps](#validation-pipeline-steps)
- [File Layout](#file-layout)
- [Dependencies](#dependencies)
- [Acceptance Criteria](#acceptance-criteria)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Problem

`ai gemini --depth standard/quick` produces markdown research docs containing web URLs and optionally arXiv citations. These docs are consumed by design and implementation work downstream. There is no automated check that:

- URLs in the output are reachable (not 404, not hallucinated)
- arXiv IDs cited correspond to real papers
- Claims attributed to papers are actually supported by paper body text

The session config already mandates: *"Citation validation required after every research doc is written."* Today this is a manual step (run `verify-research-citations.py` from the aido repo). It is skipped routinely because it requires a separate tool invocation after the research command returns. Baking it into the pipeline removes the friction.

---

## Scope

- **In scope:** `ai gemini --depth standard` and `--depth quick` output files; dead URL detection; arXiv ID paper existence + claim verification; summary appended to research doc
- **Out of scope (this design):** non-arXiv DOI/ACL citations; NLI entailment (optional extra, same pattern as aido Phase 1); deep-research output (separate format, follow-on)

---

## Prior Art

The aido repo contains a production-quality citation validator (`scripts/verify-research-citations.py` + `citation_validator/` package) built for the same class of problems — AI-generated research docs with fabricated citations. Architecture: 7-step pipeline (lychee → S2 → CrossRef → OpenAlex → S2 snippet → arXiv HTML/PDF → NLI). Fully LLM-free. Design doc: `aido/docs/designs/research-doc-validation.md`.

Key difference: aido's validator was designed for arXiv-heavy research docs (academic ML/CS corpus). Gemini grounded-search output is web-URL-heavy with arXiv citations appearing only when the topic is academic. The ai-cli-utils implementation needs to handle both gracefully and degrade cleanly when the heavy deps aren't installed.

ai-cli-utils is a **public open-source package**. The aido validator is a private development tool and cannot be referenced or shelled out to. The code must be ported and adapted.

---

## Design Decisions

### D1: Validation scope

**What classes of citations do we validate?**

| Option | What it covers | Tradeoffs |
|--------|---------------|-----------|
| A: arXiv only | arXiv IDs (regex: `arxiv.org/abs/` or `arXiv:\d{4}.\d+`) | Misses most Gemini output (web-URL-heavy). Too narrow for practical use. |
| B: Web URLs only | All `https://` links via lychee dead-link check | Catches dead URLs but misses fabricated arXiv IDs entirely. |
| **C: Both (recommended)** | lychee for all URLs + arXiv pipeline for academic citations | Covers the full output. lychee is zero-dependency (external binary); arXiv pipeline is optional extras. Degrades cleanly when neither is installed. |

**Recommendation: C.** lychee handles the common case (web URLs from Google Search grounding). The arXiv pipeline adds high-value claim verification when the topic is academic. Both are independently optional.

> **Feedback:**

---

### D2: Where code lives

**Should the citation_validator code be ported into ai-cli-utils or called externally?**

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Shell out to aido script | `subprocess.run(["python3", "~/projects/aido/scripts/verify-research-citations.py", ...])` | Couples to a private path. Breaks for all public users. Non-starter. |
| B: Separate package on PyPI | `ai-citation-validator` published independently; ai-cli-utils adds it as optional dep | Correct separation but significant overhead for what is ~600 lines of code. Overkill at this stage. |
| **C: Port into ai-cli-utils as `src/ai_cli/citation_validator/`** (recommended) | Port the aido `citation_validator/` subpackage directly, adapted for ai-cli-utils conventions | Self-contained. Testable. No new repo needed. Already ~240 lines in 6 modules; straightforward port. |
| D: Plugin hook | Configurable `[research.citation_validator_script]` in config.toml pointing to any script | Maximum flexibility but adds config surface and no default behavior for public users. |

**Recommendation: C.** The aido validator is already modular (extractor, s2_client, crossref_client, openalex_client, body_text, nli, reporter). Porting is a structured mechanical lift, not a redesign. Makes the full stack testable within ai-cli-utils CI. Option D could be layered on top later as a power-user escape hatch.

> **Feedback:**

---

### D3: Optional dependencies strategy

The validation stack has two tiers of heaviness:

- **Tier 1 (light):** `lychee` external binary (Rust, ~10MB, `brew install lychee`) — no Python dep
- **Tier 2 (medium):** `semanticscholar`, `arxiv`, `pdfplumber`, `habanero`, `pyalex`, `beautifulsoup4` — Python packages, fast to install, ~30MB combined
- **Tier 3 (heavy):** `transformers` + `torch` (CPU) — NLI model, ~500MB

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Hard dependencies | All validation deps required at install | Citation validation is valuable but not core to session management. Torch as a hard dep is a non-starter for a CLI tool. |
| **B: Optional extras (recommended)** | `pip install ai-cli-utils[citation]` adds Tier 2; `ai-cli-utils[citation-nli]` adds Tier 3 | Standard Python packaging pattern. Validation is available but not forced on users who only need session management. |
| C: Soft imports with no extras defined | Try to import; print WARN and skip if not installed | Undiscoverable. No `pip install` path advertised. |

**Recommendation: B.** Two extras:
```toml
[project.optional-dependencies]
citation = ["semanticscholar", "arxiv", "pdfplumber", "habanero", "pyalex", "beautifulsoup4"]
citation-nli = ["ai-cli-utils[citation]", "transformers", "torch"]
```

lychee is always checked via `shutil.which("lychee")`. If not found, the dead-link step is skipped with a one-line warning. This matches the aido validator's behavior.

> **Feedback:**

---

### D4: Pipeline integration — auto vs opt-in

**When does validation run?**

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| A: Separate command only | `ai research validate <file>` | No friction to existing workflow but doesn't remove the manual step that causes it to be skipped. Against the session config mandate. |
| B: Opt-in flag | `ai gemini --depth standard --validate` | Explicit but easily forgotten. Same problem as Option A. |
| **C: Auto on `--depth standard/quick`, suppressible (recommended)** | Runs automatically after synthesis writes the output file. `--no-validate` to suppress. | Satisfies session config mandate. Validation becomes the default, skipping becomes the explicit choice. |
| D: Config default | `[research] auto_validate = true/false` in config.toml | Correct for power users, but the default (`false` to be safe) re-creates the same skip problem. |

**Recommendation: C.** Auto-run after synthesis. Validation is non-blocking (see D5) so it never delays or breaks the primary output — it just appends a summary. `--no-validate` is the escape hatch. The `[research] validate = false` config key disables it globally.

**One exception:** `--depth deep-research` output is a different document format (Interactions API) and is out of scope for this design. Validation is skipped automatically when `model == "deep-research"`.

> **Feedback:**

---

### D5: Blocking behavior

**Does a FAIL verdict stop the pipeline or just report?**

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| **A: Non-blocking by default (recommended)** | Validation always runs after synthesis. FAILs are reported in the doc but exit code is 0. `--validate-strict` exits non-zero on any FAIL. | Research output is the primary deliverable. Blocking on citation issues would prevent getting the output at all, which is worse than having flagged-but-unverified citations. |
| B: Always blocking | Exit non-zero on any FAIL | Would break on any arXiv ID that S2 hasn't indexed yet (real papers do fail S2 occasionally). Causes false negatives. |
| C: Blocking on FAIL, not WARN | Exit non-zero only on hard failures (ID not found anywhere) | More nuanced but complex verdict mapping. Can add later if needed. |

**Recommendation: A.** Non-blocking by default. The value is visibility, not gatekeeping. `--validate-strict` is available for scripts/CI where strict validation is wanted.

> **Feedback:**

---

### D6: Output format

**How is the validation summary surfaced?**

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Separate sidecar file | `output.validation.md` alongside `output.md` | Two files to track; easy to separate validation data from research content. |
| B: Append to output doc | `## Citation Validation` section appended to the research markdown | Single file. Standard pattern used in aido's `validation-report.md`. Visible inline when doc is read. |
| C: Stdout only | Print summary to terminal, nothing written to file | No persistence. Loses validation data when terminal scrolls. |
| **D: Append to output doc + checkpoint JSON (recommended)** | Append `## Citation Validation` to the markdown; also save `step_04_citation_validation.json` in the run checkpoint dir for machine-readable access and resume | Best of both: human-readable inline, machine-readable for tooling, resumable if re-run. |

**Recommendation: D.** Consistent with the existing checkpoint pattern (`step_01_`, `step_02_`, `step_03_`). The markdown section uses the same PASS/WARN/FAIL/dead-link format as aido's `validation-report.md`, adapted to append style.

> **Feedback:**

---

## Architecture

### Pipeline flow (updated `run_standard()`)

```text
Step 1: Query generation          → step_01_query_generation.json
Step 2: Concurrent grounded search → step_02_search_results.json
Step 3: Synthesis → output file   → step_03_synthesis.json

Step 4: Citation validation       → step_04_citation_validation.json   [NEW]
  4a. lychee dead-link check (if lychee installed)
  4b. Extract arXiv IDs + claim sentences (always)
  4c. S2/CrossRef/OpenAlex paper existence (if semanticscholar installed)
  4d. S2 snippet + arXiv HTML/PDF body text (if semanticscholar + pdfplumber installed)
  4e. NLI entailment (if --validate-nli + transformers installed)
  4f. Append ## Citation Validation to output file
  4g. Save JSON checkpoint
```

Step 4 never raises — all errors are caught and surfaced as WARNs in the report. If no validation tools are installed, Step 4 emits a one-line note and exits cleanly.

### `validate_citations()` interface

```python
from ai_cli.citation_validator import validate_citations, ValidationReport

report: ValidationReport = validate_citations(
    doc_path=Path("/path/to/output.md"),
    run_id="20260421-...",
    run_nli=False,        # --validate-nli flag
    quiet=False,
)
# report.passes, report.warns, report.fails, report.dead_links
# report.to_markdown() → ## Citation Validation section text
```

### Graceful degradation tiers

| Tools installed | What runs |
|----------------|-----------|
| Nothing | Note: "Citation validation skipped — install ai-cli-utils[citation] to enable" |
| `lychee` binary only | Dead-link check only |
| `lychee` + `citation` extras | Full arXiv pipeline (no NLI) |
| `lychee` + `citation` + `citation-nli` extras | Full pipeline including NLI entailment |

### `## Citation Validation` section format

```markdown
## Citation Validation

*Validated: 2026-04-21T04:30:00Z — lychee + S2 + CrossRef*

| Status | Citation | Notes |
|--------|----------|-------|
| ✅ PASS | arXiv:2310.01848 — "Attention is all you need" | S2 confirmed; claim entailed (0.87) |
| ⚠️ WARN | arXiv:2601.12707 — title mismatch | S2 title: "XYZ Paper" vs cited as "ABC Paper" |
| ❌ FAIL | arXiv:9999.99999 | Not found in S2, CrossRef, or OpenAlex |
| 🔗 DEAD | https://example.com/paper | 404 |

**Summary:** 1 PASS · 1 WARN · 1 FAIL · 1 dead link
```

---

## File Layout

```text
src/ai_cli/
  citation_validator/
    __init__.py            # validate_citations(), ValidationReport
    extractor.py           # extract arXiv IDs, claim sentences, URLs from markdown
    s2_client.py           # Semantic Scholar SDK: get_paper + search_snippet
    crossref_client.py     # habanero CrossRef: parallel metadata cross-check
    openalex_client.py     # pyalex OpenAlex: tertiary metadata fallback
    body_text.py           # arXiv HTML → pdfplumber fallback chain
    nli.py                 # roberta-large-mnli entailment (optional)
    reporter.py            # ValidationReport, CitationResult, to_markdown()
  research.py              # Step 4 integrated into run_standard()

tests/
  test_citation_validator/
    test_extractor.py      # arXiv ID extraction, claim sentence detection, URL extraction
    test_s2_client.py      # S2 get_paper + search_snippet (mocked)
    test_crossref_client.py
    test_openalex_client.py
    test_body_text.py
    test_reporter.py       # to_markdown(), verdict computation
    test_validate_citations.py  # integration: full pipeline on fixture docs
  test_research.py         # updated: validate_citations called in run_standard
```

---

## Dependencies

```toml
[project.optional-dependencies]
citation = [
    "semanticscholar>=0.8",
    "arxiv>=2.0",
    "pdfplumber>=0.11",
    "habanero>=1.2",
    "pyalex>=0.15",
    "beautifulsoup4>=4.12",
]
citation-nli = [
    "ai-cli-utils[citation]",
    "transformers>=4.40",
    "torch>=2.0",
]
```

`lychee` installed separately as a system binary (`brew install lychee` / `cargo install lychee`). Not a Python dep. Detected at runtime via `shutil.which("lychee")`.

---

## Acceptance Criteria

- [ ] `ai gemini --depth standard "topic"` automatically runs citation validation after synthesis writes the output file, without `--validate` flag required
- [ ] `ai gemini --depth standard "topic" --no-validate` skips Step 4 entirely
- [ ] When no citation tools are installed, Step 4 emits one warning line and exits cleanly (no crash, no error)
- [ ] When only `lychee` is installed, dead-link check runs and arXiv pipeline is skipped with a note
- [ ] When `ai-cli-utils[citation]` is installed, full arXiv pipeline runs (S2 → CrossRef → OpenAlex → body text)
- [ ] `## Citation Validation` section appended to output markdown with correct PASS/WARN/FAIL/dead-link table
- [ ] `step_04_citation_validation.json` written to run checkpoint directory; `--resume` skips Step 4 if checkpoint exists
- [ ] `--validate-strict` exits non-zero when any FAIL verdict present
- [ ] `validate_citations()` never raises — all exceptions surfaced as WARNs in report
- [ ] Separate `ai research validate <file>` command available for validating existing docs
- [ ] 90%+ coverage on `citation_validator/` modules; all failure paths tested with mocked HTTP errors

---

## Open Questions

- **OQ1:** The aido `citation_validator/` modules (`extractor.py`, `s2_client.py`, etc.) already exist and are tested there. Should the port be a verbatim copy (and diverge independently) or should we consider publishing the aido modules as a library? Publishing would avoid divergence but adds a public library maintenance burden. At this stage, verbatim port + diverge is simpler.
- **OQ2:** Gemini research docs cite sources inline in prose (e.g., "According to [paper](https://arxiv.org/abs/1234.5678)") rather than in a references section. The extractor needs to handle both inline link format and bare `arXiv:NNNN.NNNNN` format. Test corpus needed before implementation.
- **OQ3:** Rate limiting — S2 unauthenticated is ~1 rps. A research doc with 20 citations takes ~20s for S2 alone. Should we expose `[research.citation_rate_limit_rps]` config? Default to 1 rps (S2 safe) with higher if `S2_API_KEY` is set?

---

## Approval Log

| Date | Round | Notes |
|------|-------|-------|
| | | |
