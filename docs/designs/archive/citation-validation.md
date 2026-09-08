---
title: "Citation Validation for ai gemini Research Output"
category: design
tags: [citation-validation, gemini, research, lychee, semantic-scholar, AI-CLI-50]
status: archived
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

The companion repo contains a production-quality citation validator (`scripts/verify-research-citations.py` + `citation_validator/` package) covering the arXiv pipeline: 7-step (lychee → S2 → CrossRef → OpenAlex → S2 snippet → arXiv HTML/PDF → NLI). Its design doc (`companion/docs/designs/research-doc-validation.md`) also specifies the non-arXiv DOI/ACL extension (D7–D11 below). Both are incorporated here in full.

ai-cli-utils is a **public open-source package**. The companion validator is a private development tool and cannot be referenced or shelled out to. The full stack is ported and adapted here.

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

> **Feedback:** Approved.

---

### D2: Where validation code lives

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Shell out to companion script | `subprocess.run(["python3", "~/projects/companion/..."])` | Couples to private path. Breaks for all public users. Non-starter. |
| B: Separate PyPI package | `ai-citation-validator` published independently | Correct separation but ~600 lines of code doesn't warrant a new repo at this stage. |
| **C: Port into `src/ai_cli/citation_validator/`** (recommended) | Port companion `citation_validator/` subpackage, adapted for ai-cli-utils conventions | Self-contained, testable, no new repo. Straightforward mechanical port. |
| D: Plugin hook | `[research] citation_validator_script = "/path/to/script.py"` in config.toml. If set, `validate_citations()` shells out to that script with the doc path as argument; the script is responsible for appending `## Citation Validation` to the doc. Skips the built-in pipeline entirely. | Power-user escape hatch. ~10 lines of shim code. Good for CI pipelines that run a custom validator. No default behavior for public users who don't configure it. |

**Recommendation: B + D.** Shared library on PyPI (`ai-citation-validator`) used by both ai-cli-utils and companion — no drift, single source of truth. Plugin hook (D) adds a power-user escape hatch; `[research] citation_validator_script` overrides the built-in when set.

**Shared library:** `ai-citation-validator` is a new standalone PyPI package. Both ai-cli-utils and companion declare it as a hard dependency. All `citation_validator/` code lives in that package — neither repo vendors a copy. Changes ship to the shared package first; both repos update their pinned version. The shared package bundles the Tier 2 Python deps (`semanticscholar`, `arxiv`, `pdfplumber`, etc.) as its own hard dependencies, so ai-cli-utils and companion get them transitively. NLI extra: `ai-citation-validator[nli]` — both repos can use `ai-cli-utils[citation-nli]` / `companion[citation-nli]` which pull `ai-citation-validator[nli]`.

**Prerequisite:** Create `ai-citation-validator` package repo, publish initial version to PyPI, add as dependency to companion and ai-cli-utils. This is a separate task (AI-CLI-51 or COMP-40 — TBD).

> **Feedback:** Publish as shared library. No drift. Long-term plan: migrate to companion research as the primary research command; ai-cli-utils `--depth` config will replicate current research workflow. For now, shared library keeps both in sync.

---

### D3: Optional dependencies strategy

Validation deps span three tiers:

- **Tier 1 (light):** `lychee` external binary (Rust, ~10MB) — no Python dep, detected via `shutil.which`
- **Tier 2 (medium):** `semanticscholar`, `arxiv`, `pdfplumber`, `habanero`, `pyalex`, `beautifulsoup4` — ~30MB combined, fast install
- **Tier 3 (heavy):** `transformers` + `torch` (CPU-only) — NLI model, ~500MB first-run download

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Hard dependencies | All deps required at install | `torch` as a hard dep means a ~500MB download for every `pip install ai-cli-utils`. Non-starter for a CLI session manager. |
| **B: Tier 2 hard + Tier 3 optional (recommended)** | Tier 2 deps are hard dependencies in `[project.dependencies]`; Tier 3 (NLI) in `[citation-nli]` optional extra | Validation always works out of the box (no `pip install ai-cli-utils[citation]` required). NLI stays opt-in because of the model size. |
| C: All optional extras | `[citation]` + `[citation-nli]` extras | Undiscoverable without docs. |

**Recommendation: B.** Tier 2 deps are small enough (~30MB) to be hard deps. The only thing kept optional is the NLI model (`transformers` + `torch`, ~500MB download on first use). `lychee` remains an optional external binary — detected via `shutil.which`, skipped gracefully if absent.

> **Feedback:** Make them hard dependencies. (Note: `torch`/`transformers` for NLI kept as `[citation-nli]` optional extra due to ~500MB model size — making that a hard dep would force a 500MB download on every install of the CLI.)

---

### D4: Pipeline integration — auto vs opt-in

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| A: Separate command only | `ai research validate <file>` | Doesn't remove the manual step that causes it to be skipped. |
| B: Opt-in flag | `ai gemini --depth standard --validate` | Easily forgotten. Same problem. |
| **C: Config-driven default, CLI flag to override (recommended)** | `[research] auto_validate = true` in config.toml (default). `--no-validate` flag suppresses on a per-run basis. `--validate` flag forces on when config default is `false`. | Config controls the default; CLI flag overrides it either way. Default-on satisfies the session config mandate. |
| D: Config gate only | `[research] auto_validate = false` default | Default-off recreates the skip problem. |

**Recommendation: C.** Default config ships with `auto_validate = true`. Users who want to disable globally set `auto_validate = false`. Per-run override: `--no-validate` (when default is on) or `--validate` (when default is off). No issues with this approach — both flags are straightforward Click options.

> **Feedback:** Config-driven default with opt-out flag. Default `auto_validate = true`. `--no-validate` to suppress per run. `--validate` to force on when config default is false. Approved.

---

### D5: Blocking behavior

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| **A: Non-blocking by default, configurable strictness (recommended)** | FAILs reported; exit code 0 by default. Strictness configurable via config + CLI flag. | Non-blocking default prevents false negatives (real papers occasionally fail S2 lookup). Configurable levels let CI pipelines tighten the gate. |
| B: Always blocking | Exit non-zero on any FAIL | Too aggressive for interactive use. |
| C: Block on FAIL, not WARN | Hard failures only | Subset of A with `validate_strict = "fail"`. |

**Recommendation: A.** Three strictness levels:

| Level | Config value | CLI flag | Behavior |
|-------|-------------|----------|----------|
| Off (default) | `validate_strict = "none"` | *(default)* | Exit 0 always; FAILs visible in doc only |
| Fail | `validate_strict = "fail"` | `--validate-strict fail` | Exit non-zero on any FAIL verdict |
| Warn | `validate_strict = "warn"` | `--validate-strict warn` | Exit non-zero on any FAIL or WARN verdict |

> **Feedback:** Option A, make strictness configurable.

---

### D6: Output format

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Separate sidecar file | `output.validation.md` | Two files to track. |
| B: Append to output doc | `## Citation Validation` section in `output.md` | Single file, visible inline when doc is read. |
| C: Stdout only | Terminal output only | No persistence. |
| **D: Append to output doc + checkpoint JSON (recommended)** | `## Citation Validation` appended to `output.md`; `step_04_citation_validation.json` in checkpoint dir | Human-readable inline + machine-readable for tooling. Resumable: `--resume` skips Step 4 if checkpoint exists. |

**Recommendation: D.**

> **Feedback:** Approved.

---

### D7: DOI detection scope

**How aggressively do we extract DOI citations from markdown?**

| Option | Pattern | Tradeoffs |
|--------|---------|-----------|
| A: Explicit markers only | `doi.org/` URLs and `doi:` prefixed refs | Low false-positive rate. Misses bare DOIs in text. |
| **B: Explicit + bare (recommended)** | Also match bare `10\.\d{4,}/\S+` patterns anywhere in text | Higher coverage. False positives are low risk — invalid bare patterns fail DOI resolution gracefully and produce FAIL verdicts. |

**Recommendation: B.** The regex `10\.\d{4,}/[^\s,;)]+` is well-established for bare DOI matching. Resolution via CrossRef/S2 is the authoritative gate — false-positive bare matches simply won't resolve and produce a FAIL.

**Deduplication rule:** DOIs co-occurring with an arXiv ID on the same line are skipped for separate DOI processing — the arXiv pipeline already covers that citation.

> **Feedback:** Approved.

---

### D8: ACL Anthology body text

**When S2 doesn't have indexed full text for an ACL paper (~30% of ACL corpus), do we attempt body text retrieval?**

| Option | Behavior | Tradeoffs |
|--------|----------|-----------|
| A: Metadata check only | WARN "claim unverifiable — body text not indexed" | Simpler. Leaves ~30% of ACL claims unverified. |
| **B: ACL PDF fallback (recommended)** | Fetch `aclanthology.org/{id}.pdf` + pdfplumber (same stack as arXiv PDF fallback) | Higher claim coverage. No new dependencies — pdfplumber already in Tier 2. Consistent with arXiv handling. |

**Recommendation: B.** Same stack, higher coverage, no additional deps. ACL PDF URLs follow a predictable pattern (`aclanthology.org/{year}.{venue}-{paper}.{N}.pdf`).

> **Feedback:** Approved.

---

### D9: Paywalled papers verdict

**When a paper exists (confirmed via CrossRef/S2) but body text is behind a paywall (IEEE, Springer, Nature), what verdict do we emit?**

| Option | Verdict | Tradeoffs |
|--------|---------|-----------|
| A: WARN | Same as metadata-mismatch WARNs | Indistinguishable from actual claim issues in triage. |
| **B: EXISTENCE_ONLY (recommended)** | Separate verdict tier: paper confirmed, claim unverifiable | Clean triage signal. Requires a fourth verdict type throughout reporter and downstream. |

**Recommendation: B.** The distinction between "paper found but claim can't be checked" vs "something looks wrong with this citation" is genuinely useful during triage. The reporter cost (one extra verdict enum value) is low.

> **Feedback:** Approved.

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
| **A: Rely on DOI detection + unknown venue surfacing (recommended)** | Modern NeurIPS/ICML/ICLR papers have DOIs — covered by D7. For URLs that look academic but match no known extractor pattern, emit an `UNKNOWN_VENUE` notice in the report. | High coverage for DOI-backed papers. Unknown venues surface automatically for triage rather than being silently skipped. |
| B: Per-venue URL pattern detection | Per-venue regex for `proceedings.mlr.press/`, `openreview.net/`, `papers.nips.cc/` etc. | High per-venue implementation cost for marginal gain given DOI coverage already handles most modern papers. |

**Recommendation: A.** DOI detection covers the majority of major venue papers. Unknown venues are surfaced — not silently skipped.

**Unknown venue surfacing mechanism:** The extractor maintains a list of known academic URL patterns (arXiv, doi.org, aclanthology.org, semanticscholar.org, openreview.net, proceedings.mlr.press, papers.nips.cc). Any URL that matches a heuristic "looks academic" pattern (e.g., contains `/paper`, `/abs/`, `/proceedings/`, `conference`, `workshop`, or is linked with citation language in the surrounding text) but doesn't match any known extractor is collected as an `UnknownVenueCitation`. The validation report includes an `### Unrecognized Citation Patterns` subsection listing these URLs with their surrounding context. This surfaces naturally during UAT and gives a basis for deciding whether to add a task or handle on the spot.

```python
@dataclass
class UnknownVenueCitation:
    url: str
    context: str  # surrounding sentence for triage
    reason: str  # e.g. "URL path contains /proceedings/ but no known extractor matched"
```

> **Feedback:** Approved. Add recursive/surfacing mechanism for unknown venues — flag them in the report so we can decide whether to add a task or handle on the spot.

---

### D11: Data model — arXiv vs non-arXiv citations

**How to represent DOI/ACL citations in the data model alongside arXiv citations?**

| Option | Description | Tradeoffs |
|--------|-------------|-----------|
| A: Parallel dataclasses | `ArxivCitationResult` and `NonArxivCitationResult`; `ValidationReport` holds both lists | Zero regression but duplicates fields and requires callers to handle two types. |
| **B: Unified `CitationResult` (recommended)** | `CitationResult(citation_id, citation_type, verdict, ...)` covers all citation types. `ValidationReport.results: list[CitationResult]`. Requires refactoring the arXiv pipeline to use the unified model. | Clean unified API. One type to import, one list to iterate, one set of tests for the data model. `to_markdown()` groups by `citation_type` for display. Worth the refactor. |

**Recommendation: B.** We're building this from scratch in the shared library — do it right. The arXiv pipeline refactor is mechanical (rename fields, update callers). Unified model means callers only ever deal with one type.

```python
from typing import Literal

CitationType = Literal["arxiv", "doi", "acl"]
Verdict = Literal["PASS", "WARN", "FAIL", "EXISTENCE_ONLY"]


@dataclass
class CitationResult:
    citation_id: str  # arXiv ID, DOI string, or ACL ID
    citation_type: CitationType
    verdict: Verdict
    title_found: str | None = None
    claim_snippet: str | None = None
    nli_score: float | None = None
    error: str | None = None


@dataclass
class UnknownVenueCitation:
    url: str
    context: str  # surrounding sentence for triage
    reason: str  # why it didn't match any known extractor


@dataclass
class ValidationReport:
    results: list[CitationResult]  # all arXiv + DOI + ACL
    dead_links: list[str]
    unknown_venues: list[UnknownVenueCitation]
    tools_used: list[str]
    validated_at: str

    def passes(self) -> int: ...  # count where verdict == "PASS"
    def warns(self) -> int: ...  # count where verdict == "WARN"
    def fails(self) -> int: ...  # count where verdict == "FAIL"
    def existence_only(self) -> int: ...  # count where verdict == "EXISTENCE_ONLY"
    def by_type(self, t: CitationType) -> list[CitationResult]: ...
    def to_markdown(self) -> str: ...  # groups by citation_type for ## Citation Validation section
```

> **Feedback:** Option B — unified model. Refactor. Let's do it right.

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

Code lives in the `ai-citation-validator` shared package; both ai-cli-utils and companion import from it:

```python
from citation_validator import validate_citations, ValidationReport, CitationResult

report: ValidationReport = validate_citations(
    doc_path=Path("/path/to/output.md"),
    run_id="20260421-...",
    run_nli=False,  # --validate-nli / citation-nli extra
    strict="none",  # "none" | "fail" | "warn"
    quiet=False,
    validator_script=None,  # D2 plugin hook: path to external script, overrides built-in
)
# report.results           ← list[CitationResult] (arXiv + DOI + ACL unified)
# report.dead_links
# report.unknown_venues    ← list[UnknownVenueCitation] (D10)
# report.to_markdown()     → ## Citation Validation section text
```

**Plugin hook behaviour (D2-D):** if `validator_script` is set (from `[research] citation_validator_script` config), `validate_citations()` calls `subprocess.run([validator_script, str(doc_path)])` and returns immediately. The script appends `## Citation Validation` to the doc. Built-in pipeline is skipped entirely.

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

Tier 2 deps are hard dependencies — always available after `pip install ai-cli-utils`. The only optional layer is the NLI model.

| Tools installed | What runs |
|----------------|-----------|
| Base install (no lychee, no NLI) | Full arXiv + DOI/ACL pipeline; dead-link step skipped with one-line note |
| Base + `lychee` binary | Full arXiv + DOI/ACL pipeline + dead-link check |
| Base + `lychee` + `citation-nli` extra | Full pipeline including NLI entailment |
| Plugin hook configured | External script called; built-in pipeline skipped |

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

### Unrecognized Citation Patterns

The following URLs appear to reference academic content but did not match any
known extractor pattern (arXiv, DOI, ACL, etc.). Review and add extractor
support if needed.

| URL | Context |
|-----|---------|
| https://proceedings.example.org/2023/paper42 | "...as shown by Smith et al. [paper](https://proceedings.example.org/2023/paper42)..." |

**Summary:** 2 PASS · 1 WARN · 2 FAIL · 1 EXISTENCE_ONLY · 1 dead link · 1 unrecognized
```

---

## File Layout

### `ai-citation-validator` shared package (new repo — prerequisite task)

```text
src/citation_validator/
  __init__.py            # validate_citations(), ValidationReport, CitationResult, UnknownVenueCitation
  extractor.py           # extract arXiv IDs, DOIs, ACL URLs, unknown venues, claim sentences
  s2_client.py           # Semantic Scholar SDK: get_paper("ARXIV:|DOI:|ACL:") + search_snippet
  crossref_client.py     # habanero: DOI metadata + title search fallback
  openalex_client.py     # pyalex: tertiary metadata fallback
  body_text.py           # arXiv HTML → PDF fallback; ACL PDF fallback
  nli.py                 # roberta-large-mnli entailment (nli extra)
  reporter.py            # ValidationReport, CitationResult (unified D11), to_markdown()
  venues.py              # known venue URL patterns + "looks academic" heuristic (D10)

tests/
  test_extractor.py      # arXiv, DOI, ACL, bare DOI extraction; dedup; unknown venue detection
  test_s2_client.py      # get_paper all citation_type prefixes, search_snippet (mocked)
  test_crossref_client.py
  test_openalex_client.py
  test_body_text.py      # arXiv HTML/PDF, ACL PDF (mocked HTTP)
  test_reporter.py       # to_markdown(), all 5 verdict types, by_type(), summary line
  test_venues.py         # known pattern matching, "looks academic" heuristic
  test_validate_citations.py  # integration: fixture docs with arXiv + DOI + unknown venue
```

### ai-cli-utils (this repo)

```text
src/ai_cli/
  research.py    # Step 4: calls validate_citations() from citation_validator package;
                 # reads auto_validate, validate_strict, citation_validator_script from config

tests/
  test_research.py   # updated: auto_validate config, --no-validate, --validate-strict, plugin hook
```

### companion (existing)

```text
scripts/
  verify-research-citations.py   # updated to import from citation_validator package
  citation_validator/            # REMOVED — replaced by shared package dependency
```

---

## Dependencies

### `ai-citation-validator` package (pyproject.toml)

```toml
[project]
dependencies = [
    "semanticscholar>=0.8",
    "arxiv>=2.0",
    "pdfplumber>=0.11",
    "habanero>=1.2",
    "pyalex>=0.15",
    "beautifulsoup4>=4.12",
]

[project.optional-dependencies]
nli = ["transformers>=4.40", "torch>=2.0"]
```

### ai-cli-utils (this repo)

```toml
[project]
dependencies = [
    "ai-citation-validator>=0.1",
    # ... existing deps
]

[project.optional-dependencies]
citation-nli = ["ai-citation-validator[nli]"]
```

### companion

```toml
[project]
dependencies = [
    "ai-citation-validator>=0.1",
    # ... existing deps
]
```

`lychee` installed separately (`brew install lychee` / `cargo install lychee`). Detected at runtime via `shutil.which("lychee")`. Skipped gracefully with a one-line note if absent.

---

## Acceptance Criteria

**Pipeline integration:**
- [ ] `ai gemini --depth standard "topic"` automatically runs citation validation after synthesis (D4: `auto_validate = true` default)
- [ ] `[research] auto_validate = false` in config.toml globally disables Step 4
- [ ] `--no-validate` flag suppresses Step 4 when config default is on
- [ ] `--validate` flag forces Step 4 when config default is off
- [ ] `[research] validate_strict = "fail"` causes exit non-zero on any FAIL verdict
- [ ] `[research] validate_strict = "warn"` causes exit non-zero on any FAIL or WARN verdict
- [ ] `--validate-strict fail` and `--validate-strict warn` CLI flags override config per run
- [ ] `[research] citation_validator_script = "/path/to/script"` calls external script instead of built-in pipeline (D2 plugin hook)
- [ ] `step_04_citation_validation.json` written to run checkpoint; `--resume` skips Step 4 if checkpoint exists
- [ ] `validate_citations()` never raises — all exceptions surfaced as WARNs in report
- [ ] Separate `ai research validate <file>` command validates any existing research doc

**arXiv citations:**
- [ ] arXiv IDs extracted from `arxiv.org/abs/`, `arXiv:NNNN.NNNNN` patterns
- [ ] S2 confirms paper exists and title/authors match; WARN on mismatch
- [ ] S2 snippet search checks claim appears in paper body; HTML fallback then PDF when S2 misses
- [ ] FAIL verdict when paper not found in S2, CrossRef, or OpenAlex

**Non-arXiv DOI/ACL citations:**
- [ ] `doi.org/` URLs, `doi:` prefixes, and bare `10.XXXX/...` patterns extracted (D7)
- [ ] ACL Anthology URLs (`aclanthology.org/`) extracted and validated
- [ ] DOIs co-occurring with arXiv IDs on the same line are not double-counted
- [ ] S2 resolves DOI citations via `get_paper("DOI:{doi}")`
- [ ] CrossRef validates title/authors in parallel
- [ ] ACL PDF fallback via `aclanthology.org/{id}.pdf` + pdfplumber when S2 body text absent (D8)
- [ ] EXISTENCE_ONLY verdict emitted for papers confirmed but body text paywalled (D9)
- [ ] FAIL verdict when DOI/ACL paper not found in CrossRef, S2, or OpenAlex

**Unknown venue surfacing (D10):**
- [ ] URLs matching "looks academic" heuristic but no known extractor pattern collected as `UnknownVenueCitation`
- [ ] `### Unrecognized Citation Patterns` subsection appears in report when any unknown venues found
- [ ] Each unknown venue entry includes the URL and surrounding context sentence
- [ ] Unknown venues counted in summary line: `N unrecognized`

**Output:**
- [ ] `## Citation Validation` section appended with arXiv, DOI/ACL, dead links, and unrecognized subsections
- [ ] Summary line: `N PASS · N WARN · N FAIL · N EXISTENCE_ONLY · N dead links · N unrecognized`
- [ ] When lychee not installed, dead-link step skipped with one-line note; rest of pipeline runs normally

**Coverage:**
- [ ] 90%+ line coverage on `citation_validator/` modules
- [ ] All failure paths tested: HTTP errors, DNS failures, S2 misses, CrossRef 404s, pdfplumber parse errors, malformed DOI patterns
- [ ] At least one fixture doc with a known fabricated arXiv ID used in integration test
- [ ] Plugin hook: test that external script is called and built-in pipeline is skipped

---

## Open Questions

~~**OQ1:** S2 unauthenticated rate limit is ~1 rps. A research doc with 20 citations = ~20s for S2 alone.~~ **Resolved:** No S2 API key for now (request pending in companion roadmap; not guaranteed to be granted). Work within the 1 rps unauthenticated limit. Wall time is acceptable for a post-synthesis step — not a blocking issue.

~~**OQ2:** Gemini research docs cite inline in prose — extractor must handle both inline link format and bare `arXiv:NNNN.NNNNN`.~~ **Resolved:** This is an implementation note, not a design question. The extractor handles all formats: inline markdown links (`[title](url)`), bare `arXiv:NNNN.NNNNN`, `doi:10.XXXX/...` prefixes, and bare `10.XXXX/...` patterns. The fixture docs in `tests/fixtures/` will be seeded from real Gemini output before implementation.

~~**OQ3:** Port into ai-cli-utils and diverge, or publish as shared library?~~ **Resolved:** Publish as `ai-citation-validator` shared PyPI package. Both ai-cli-utils and companion depend on it. No drift possible. See D2 and File Layout. Prerequisite: create `ai-citation-validator` repo and publish initial version (separate task).

---

## Approval Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-04-21 | Round 1 | D1 approved. D2: C + D both (plugin hook added). D3: Tier 2 hard deps; Tier 3 (NLI/torch) stays optional extra. D4: config-driven default `auto_validate = true`, `--no-validate`/`--validate` CLI flags. D5: non-blocking default, configurable `validate_strict` (none/fail/warn). D6 approved. D7–D11 approved. D10: unknown venue surfacing mechanism added. |
| 2026-04-21 | Round 2 | D2 revised: B + D — shared library (`ai-citation-validator` on PyPI) replaces port-into-ai-cli-utils. OQ3 resolved. D11 revised: Option B — unified `CitationResult` model, refactor arXiv pipeline. OQ1 resolved (1 rps, no action). OQ2 closed (implementation note). All decisions fully resolved. |
