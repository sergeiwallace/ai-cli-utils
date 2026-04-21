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
  - [D1: Validation scope](#d1-validation-scope)
  - [D2: Where validation code lives](#d2-where-code-lives)
  - [D3: Optional dependencies strategy](#d3-optional-dependencies-strategy)
  - [D4: Pipeline integration — auto vs opt-in](#d4-pipeline-integration)
  - [D5: Blocking behavior](#d5-blocking-behavior)
  - [D6: Output format](#d6-output-format)
  - [D7: DOI detection scope](#d7-doi-detection-scope)
  - [D8: ACL Anthology body text](#d8-acl-anthology-body-text)
  - [D9: Paywalled papers verdict](#d9-paywalled-papers-verdict)
  - [D10: Other academic venues](#d10-other-academic-venues)
  - [D11: Data model — arXiv vs non-arXiv citations](#d11-data-model)
- [Architecture](#architecture)
- [Validation Pipeline Steps](#validation-pipeline-steps)
- [File Layout](#file-layout)
- [Dependencies](#dependencies)
- [Acceptance Criteria](#acceptance-criteria)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Problem

`ai gemini --depth standard/quick` produces markdown research docs containing web URLs, arXiv citations, and non-arXiv academic citations (DOIs, ACL Anthology links). These docs are consumed by design and implementation work downstream. There is no automated check that:

- URLs in the output are reachable (not 404, not hallucinated)
- arXiv IDs correspond to real papers with matching titles and authors
- Non-arXiv DOIs and ACL citations resolve to real papers
- Claims attributed to papers are actually supported by the paper body text

The session config already mandates: *"Citation validation required after every research doc is written."* Today this is a manual step. It is skipped routinely because it requires a separate tool invocation after the research command returns. Baking it into the pipeline removes the friction entirely.

---

## Scope

- **In scope:** `ai gemini --depth standard` and `--depth quick` output files; dead URL detection; arXiv paper existence + claim verification; non-arXiv DOI paper existence + claim verification; ACL Anthology papers; NLI entailment (optional extra); separate `ai research validate <file>` command for existing docs
- **Out of scope:** `--depth deep-research` (different document format, separate task); inline code claims; subjective quality judgments

---

## Prior Art

The aido repo contains a production-quality citation validator (`scripts/verify-research-citations.py` + `citation_validator/` package) covering the arXiv pipeline: 7-step (lychee → S2 → CrossRef → OpenAlex → S2 snippet → arXiv HTML/PDF → NLI). Its design doc (`aido/docs/designs/research-doc-validation.md`) also specifies the non-arXiv DOI/ACL extension (D7–D11 below). Both are incorporated here in full.

ai-cli-utils is a **public open-source package**. The aido validator is a private development tool and cannot be referenced or shelled out to. The full stack is ported and adapted here.

---

## Design Decisions

### D1: Validation scope

**What classes of citations do we validate?**

| Option | What it covers | Tradeoffs |
|--------|---------------|-----------|
| A: arXiv only | arXiv IDs | Misses DOIs and most Gemini web-URL output. Too narrow. |
| B: Web URLs only | All `https://` links via lychee | Catches dead URLs but misses fabricated academic citations entirely. |
| **C: All (recommended)** | lychee for all URLs + arXiv pipeline + DOI/ACL pipeline | Full coverage. Each layer degrades independently when deps are absent. |

**Recommendation: C.**

> **Feedback:**

---

### D2: Where validation code lives

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Shell out to aido script | `subprocess.run(["python3", "~/projects/aido/..."])` | Couples to private path. Breaks for all public users. Non-starter. |
| B: Separate PyPI package | `ai-citation-validator` published independently | Correct separation but ~600 lines of code doesn't warrant a new repo at this stage. |
| **C: Port into `src/ai_cli/citation_validator/`** (recommended) | Port aido `citation_validator/` subpackage, adapted for ai-cli-utils conventions | Self-contained, testable, no new repo. Straightforward mechanical port. |
| D: Plugin hook | Configurable `[research.citation_validator_script]` in config.toml | No default behavior for public users. Power-user escape hatch only. |

**Recommendation: C.** The aido validator is already modular (extractor, s2_client, crossref_client, openalex_client, body_text, nli, reporter). Option D can be layered on top later.

> **Feedback:**

---

### D3: Optional dependencies strategy

Validation deps span three tiers:

- **Tier 1 (light):** `lychee` external binary (Rust, ~10MB) — no Python dep, detected via `shutil.which`
- **Tier 2 (medium):** `semanticscholar`, `arxiv`, `pdfplumber`, `habanero`, `pyalex`, `beautifulsoup4` — ~30MB combined
- **Tier 3 (heavy):** `transformers` + `torch` (CPU-only) — NLI model, ~500MB

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Hard dependencies | All deps required at install | `torch` as a hard dep for a CLI session manager is a non-starter. |
| **B: Optional extras (recommended)** | `pip install ai-cli-utils[citation]` adds Tier 2; `[citation-nli]` adds Tier 3 | Standard Python packaging. Validation available but not forced. |
| C: Soft imports, no extras defined | Try to import; warn and skip if absent | Undiscoverable — no `pip install` path advertised. |

**Recommendation: B.**

> **Feedback:**

---

### D4: Pipeline integration — auto vs opt-in

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| A: Separate command only | `ai research validate <file>` | Doesn't remove the manual step that causes it to be skipped. |
| B: Opt-in flag | `ai gemini --depth standard --validate` | Easily forgotten. Same problem. |
| **C: Auto on `--depth standard/quick`, suppressible (recommended)** | Runs automatically after synthesis. `--no-validate` to suppress. | Satisfies session config mandate. Skipping is the explicit choice, not the default. |
| D: Config gate | `[research] auto_validate = false` | Default-off recreates the skip problem. |

**Recommendation: C.** Validation is non-blocking (D5) so it never holds up the primary output. `--no-validate` and `[research] validate = false` are the escape hatches.

> **Feedback:**

---

### D5: Blocking behavior

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| **A: Non-blocking by default (recommended)** | FAILs reported in doc; exit code 0. `--validate-strict` exits non-zero on any FAIL. | Real papers occasionally fail S2 lookup. Blocking would cause false negatives. |
| B: Always blocking | Exit non-zero on any FAIL | Too aggressive. |
| C: Block on FAIL only, not WARN | Exit non-zero on hard failures | Can be added later if needed. |

**Recommendation: A.**

> **Feedback:**

---

### D6: Output format

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Separate sidecar file | `output.validation.md` | Two files to track. |
| B: Append to output doc | `## Citation Validation` section in `output.md` | Single file, visible inline when doc is read. |
| C: Stdout only | Terminal output only | No persistence. |
| **D: Append to output doc + checkpoint JSON (recommended)** | `## Citation Validation` appended to `output.md`; `step_04_citation_validation.json` in checkpoint dir | Human-readable inline + machine-readable for tooling. Resumable: `--resume` skips Step 4 if checkpoint exists. |

**Recommendation: D.**

> **Feedback:**

---

### D7: DOI detection scope

**How aggressively do we extract DOI citations from markdown?**

| Option | Pattern | Tradeoffs |
|--------|---------|-----------|
| A: Explicit markers only | `doi.org/` URLs and `doi:` prefixed refs | Low false-positive rate. Misses bare DOIs in text. |
| **B: Explicit + bare (recommended)** | Also match bare `10\.\d{4,}/\S+` patterns anywhere in text | Higher coverage. False positives are low risk — invalid bare patterns fail DOI resolution gracefully and produce FAIL verdicts. |

**Recommendation: B.** The regex `10\.\d{4,}/[^\s,;)]+` is well-established for bare DOI matching. Resolution via CrossRef/S2 is the authoritative gate — false-positive bare matches simply won't resolve and produce a FAIL.

**Deduplication rule:** DOIs co-occurring with an arXiv ID on the same line are skipped for separate DOI processing — the arXiv pipeline already covers that citation.

> **Feedback:**

---

### D8: ACL Anthology body text

**When S2 doesn't have indexed full text for an ACL paper (~30% of ACL corpus), do we attempt body text retrieval?**

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| A: Metadata check only | WARN "claim unverifiable — body text not indexed" | Simpler. Leaves ~30% of ACL claims unverified. |
| **B: ACL PDF fallback (recommended)** | Fetch `aclanthology.org/{id}.pdf` + pdfplumber (same stack as arXiv PDF fallback) | Higher claim coverage. No new dependencies — pdfplumber already in Tier 2. Consistent with arXiv handling. |

**Recommendation: B.** Same stack, higher coverage, no additional deps. ACL PDF URLs follow a predictable pattern (`aclanthology.org/{year}.{venue}-{paper}.{N}.pdf`).

> **Feedback:**

---

### D9: Paywalled papers verdict

**When a paper exists (confirmed via CrossRef/S2) but body text is behind a paywall (IEEE, Springer, Nature), what verdict do we emit?**

| Option | Verdict | Tradeoffs |
|--------|---------|-----------|
| A: WARN | Same as metadata-mismatch WARNs | Indistinguishable from actual claim issues in triage. |
| **B: EXISTENCE_ONLY (recommended)** | Separate verdict tier: paper confirmed, claim unverifiable | Clean triage signal. Requires a fourth verdict type throughout reporter and downstream. |

**Recommendation: B.** The distinction between "paper found but claim can't be checked" vs "something looks wrong with this citation" is genuinely useful during triage. The reporter cost (one extra verdict enum value) is low.

**Full verdict taxonomy:**

| Verdict | Meaning |
|---------|---------|
| `PASS` | Paper found; claim appears in body text; NLI entailed (if enabled) |
| `WARN` | Paper found; title/author mismatch, or claim not found in body text |
| `FAIL` | Paper not found in any source (S2, CrossRef, OpenAlex) |
| `EXISTENCE_ONLY` | Paper confirmed; body text behind paywall — claim unverifiable |
| `DEAD` | URL returns 4xx/5xx or DNS failure (lychee) |

> **Feedback:**

---

### D10: Other academic venues

**NeurIPS, ICML, ICLR, and similar proceedings appear as bespoke URLs with no uniform DOI schema.**

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| **A: Rely on DOI detection (recommended)** | Modern NeurIPS/ICML/ICLR papers have DOIs. The D7 DOI pipeline covers them. Older papers without DOIs are out of scope. | NeurIPS 2018+ and most ICML/ICLR papers have `proceedings.mlr.press` or OpenReview DOIs. Coverage is high without per-venue regex. |
| B: Per-venue URL pattern detection | Per-venue regex for `proceedings.mlr.press/`, `openreview.net/`, `papers.nips.cc/` etc. | Each venue needs its own extractor + body text fetcher. High per-venue implementation cost for marginal coverage gain given DOI coverage. |

**Recommendation: A.** DOI detection already covers the majority of major venue papers. Per-venue URL detection is a follow-on when specific coverage gaps are observed in practice.

> **Feedback:**

---

### D11: Data model — arXiv vs non-arXiv citations

**How to represent DOI/ACL citations in the data model alongside arXiv citations?**

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| **A: Parallel dataclasses (recommended)** | `ArxivCitationResult` and `NonArxivCitationResult` alongside each other; `ValidationReport` holds both lists | Zero regression to arXiv pipeline. Clean type separation. Parallel lists in report. |
| B: Generalize `CitationResult` | Unified `CitationResult(citation_id, citation_type, verdict, ...)` | Cleaner unified model but requires refactoring arXiv pipeline and all its callers. Regression risk. |

**Recommendation: A.** Pre-v1 codebase — clean-slate design preferred — but the arXiv pipeline is already the tested baseline. Parallel dataclasses keep the two paths independently modifiable and testable.

```python
@dataclass
class ArxivCitationResult:
    arxiv_id: str
    verdict: Literal["PASS", "WARN", "FAIL"]
    title_found: str | None
    claim_snippet: str | None
    nli_score: float | None
    error: str | None

@dataclass
class NonArxivCitationResult:
    citation_id: str              # DOI string or ACL ID
    citation_type: Literal["doi", "acl"]
    verdict: Literal["PASS", "WARN", "FAIL", "EXISTENCE_ONLY"]
    title_found: str | None
    claim_snippet: str | None
    nli_score: float | None
    error: str | None

@dataclass
class ValidationReport:
    arxiv_results: list[ArxivCitationResult]
    non_arxiv_results: list[NonArxivCitationResult]
    dead_links: list[str]
    tools_used: list[str]
    validated_at: str
    def to_markdown(self) -> str: ...
    def passes(self) -> int: ...
    def warns(self) -> int: ...
    def fails(self) -> int: ...
```

> **Feedback:**

---

## Architecture

### Pipeline flow (updated `run_standard()`)

```text
Step 1: Query generation           → step_01_query_generation.json
Step 2: Concurrent grounded search → step_02_search_results.json
Step 3: Synthesis → output file    → step_03_synthesis.json

Step 4: Citation validation        → step_04_citation_validation.json   [NEW]
  4a. lychee dead-link check on entire output file (if lychee installed)
  4b. Extract arXiv IDs + claim sentences
  4c. Extract DOIs (doi.org/ links, doi: prefixes, bare 10.XXXX/...) + ACL URLs
  4d. Deduplicate: skip DOIs co-occurring with arXiv IDs on same line
  4e. arXiv pipeline: S2 → CrossRef → OpenAlex → body text (HTML → PDF) per arXiv ID
  4f. Non-arXiv pipeline: S2 by DOI → CrossRef by DOI → ACL PDF fallback per non-arXiv citation
  4g. NLI entailment on best snippet per citation (if --validate-nli + transformers installed)
  4h. Append ## Citation Validation section to output file
  4i. Save JSON checkpoint
```

Step 4 never raises. All exceptions are caught and surfaced as WARNs. If no validation tools are installed, Step 4 emits one line and exits cleanly.

### `validate_citations()` interface

```python
from ai_cli.citation_validator import validate_citations, ValidationReport

report: ValidationReport = validate_citations(
    doc_path=Path("/path/to/output.md"),
    run_id="20260421-...",
    run_nli=False,
    quiet=False,
)
# report.arxiv_results, report.non_arxiv_results, report.dead_links
# report.to_markdown() → ## Citation Validation section text
```

### Non-arXiv pipeline per citation

```text
For each DOI / ACL citation:
  1. Resolve to S2 paper: get_paper("DOI:{doi}") or get_paper("ACL:{acl_id}")
  2. CrossRef by DOI in parallel → cross-check title/authors
  3. If ACL and S2 miss → CrossRef title search fallback
  4. S2 snippet search on best claim sentence (using S2 paper ID from step 1)
  5. If ACL paper and snippet miss → fetch aclanthology.org/{id}.pdf + pdfplumber
  6. If DOI paper and body text available via S2 HTML → regex search
  7. Emit verdict:
     - PASS: paper found + claim in body text
     - WARN: paper found + title mismatch or claim not found
     - FAIL: paper not found in S2, CrossRef, or OpenAlex
     - EXISTENCE_ONLY: paper confirmed + body text paywalled
```

### Graceful degradation tiers

| Tools installed | What runs |
|----------------|-----------|
| Nothing | One-line note: "install `ai-cli-utils[citation]` to enable validation" |
| `lychee` only | Dead-link check only |
| `lychee` + `citation` extras | Full arXiv + DOI/ACL pipeline (no NLI) |
| All + `citation-nli` | Full pipeline including NLI entailment |

### `## Citation Validation` section format

```markdown
## Citation Validation

*Validated: 2026-04-21T04:30:00Z — lychee + S2 + CrossRef + OpenAlex*

### arXiv Citations

| Status | Citation | Notes |
|--------|----------|-------|
| ✅ PASS | arXiv:2310.01848 — "Attention is all you need" | S2 confirmed; claim entailed (0.87) |
| ⚠️ WARN | arXiv:2601.12707 — title mismatch | S2 title: "XYZ Paper" vs cited as "ABC Paper" |
| ❌ FAIL | arXiv:9999.99999 | Not found in S2, CrossRef, or OpenAlex |

### DOI / ACL Citations

| Status | Citation | Notes |
|--------|----------|-------|
| ✅ PASS | doi:10.18653/v1/2023.acl-long.1 — "Some ACL Paper" | S2 confirmed; claim found in body |
| 🔒 EXISTENCE_ONLY | doi:10.1109/CVPR.2023.00001 | Paper confirmed (IEEE); body text paywalled |
| ❌ FAIL | doi:10.9999/fake.doi | Not found in CrossRef or S2 |

### Dead Links

| URL | Status |
|-----|--------|
| https://example.com/paper | 404 |

**Summary:** 2 PASS · 1 WARN · 2 FAIL · 1 EXISTENCE_ONLY · 1 dead link
```

---

## File Layout

```text
src/ai_cli/
  citation_validator/
    __init__.py            # validate_citations(), ValidationReport
    extractor.py           # extract arXiv IDs, DOIs, ACL URLs, claim sentences, all URLs
    s2_client.py           # Semantic Scholar SDK: get_paper (arXiv + DOI) + search_snippet
    crossref_client.py     # habanero: DOI metadata + title search fallback for ACL
    openalex_client.py     # pyalex: tertiary metadata fallback
    body_text.py           # arXiv HTML → PDF fallback; ACL PDF fallback
    nli.py                 # roberta-large-mnli entailment (optional)
    reporter.py            # ValidationReport, ArxivCitationResult, NonArxivCitationResult,
                           # to_markdown(), verdict computation
  research.py              # Step 4 integrated into run_standard()

tests/
  test_citation_validator/
    test_extractor.py      # arXiv ID, DOI, ACL URL, bare DOI, URL extraction; deduplication
    test_s2_client.py      # get_paper (arXiv + DOI prefix), search_snippet (mocked)
    test_crossref_client.py  # DOI metadata, title search fallback (mocked)
    test_openalex_client.py
    test_body_text.py      # arXiv HTML/PDF, ACL PDF fallback (mocked HTTP)
    test_reporter.py       # to_markdown(), all verdict types, mixed arXiv + non-arXiv
    test_validate_citations.py  # integration: fixture docs with known arXiv + DOI citations
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

`lychee` installed separately (`brew install lychee` / `cargo install lychee`). Detected at runtime via `shutil.which("lychee")`.

---

## Acceptance Criteria

**Pipeline integration:**
- [ ] `ai gemini --depth standard "topic"` automatically runs citation validation after synthesis; no extra flag required
- [ ] `ai gemini --depth standard "topic" --no-validate` skips Step 4 entirely
- [ ] `[research] validate = false` in config.toml globally disables Step 4
- [ ] `--validate-strict` exits non-zero when any FAIL verdict present
- [ ] `step_04_citation_validation.json` written to run checkpoint directory; `--resume` skips Step 4 if checkpoint exists
- [ ] `validate_citations()` never raises — all exceptions surfaced as WARNs in report
- [ ] Separate `ai research validate <file>` command validates any existing research doc

**arXiv citations:**
- [ ] arXiv IDs extracted from `arxiv.org/abs/`, `arXiv:NNNN.NNNNN` patterns in markdown
- [ ] S2 confirms paper exists and title/authors match; WARN on mismatch
- [ ] S2 snippet search checks claim appears in paper body; HTML/PDF fallback when S2 misses
- [ ] FAIL verdict when paper not found in S2, CrossRef, or OpenAlex

**Non-arXiv DOI/ACL citations:**
- [ ] `doi.org/` URLs, `doi:` prefixes, and bare `10.XXXX/...` patterns extracted
- [ ] ACL Anthology URLs (`aclanthology.org/`) extracted and validated
- [ ] DOIs co-occurring with arXiv IDs on the same line are not double-counted
- [ ] S2 resolves DOI citations via `get_paper("DOI:{doi}")` 
- [ ] CrossRef validates title/authors in parallel
- [ ] ACL PDF fallback via `aclanthology.org/{id}.pdf` + pdfplumber when S2 body text absent
- [ ] EXISTENCE_ONLY verdict emitted for papers confirmed but body text paywalled
- [ ] FAIL verdict when DOI/ACL paper not found in CrossRef, S2, or OpenAlex

**Output:**
- [ ] `## Citation Validation` section appended with separate arXiv and DOI/ACL subsections
- [ ] Dead links listed in their own subsection
- [ ] Summary line: `N PASS · N WARN · N FAIL · N EXISTENCE_ONLY · N dead links`
- [ ] Graceful degradation: when no tools installed, one-line note emitted, no crash

**Coverage:**
- [ ] 90%+ line coverage on `citation_validator/` modules
- [ ] All failure paths tested: HTTP errors, DNS failures, S2 misses, CrossRef 404s, pdfplumber parse errors
- [ ] At least one fixture doc with known fabricated citation used in integration test

---

## Open Questions

- **OQ1:** S2 unauthenticated rate limit is ~1 rps. A research doc with 20 citations = ~20s for S2 alone, ~40s with DOI pipeline. Should `[research] citation_s2_api_key` be documented at install? Default 1 rps; authenticated S2 allows higher. Affects wall time for Step 4.
- **OQ2:** Gemini research docs cite inline in prose (e.g., "According to [paper](https://arxiv.org/abs/1234.5678)"). The extractor must handle inline link format, bare `arXiv:NNNN.NNNNN`, and DOI variants. A fixture corpus of real Gemini output should be assembled before implementation to validate extraction coverage.
- **OQ3:** Port strategy — the aido `citation_validator/` modules are the authoritative implementation. Verbatim port then diverge independently vs. publishing as a shared library? At this stage, verbatim port is simpler. Flag if the two implementations drift significantly.

---

## Approval Log

| Date | Round | Notes |
|------|-------|-------|
| | | |
