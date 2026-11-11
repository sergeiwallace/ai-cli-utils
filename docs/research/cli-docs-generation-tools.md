---
title: "Generating CLI help, CLI reference docs and wiki pages from one source"
category: research
tags: [research, cli, click, documentation, wiki, docs-as-code, llm, agents]
status: complete
source: "claude-adhoc-2026-11-11"
template_version: "research-1.2.0"
delegation_provenance:
  version: 2
  contributors:
    - {role: research-leg, family: claude, model: opus, persona: researcher, scope: "Click help and Markdown generators, drift checks, wiki publishing, docs sites"}
    - {role: research-leg, family: claude, model: opus, persona: researcher, scope: "agentic and LLM documentation generation, accuracy evidence, honesty mechanisms"}
---

# Generating CLI help, CLI reference docs and wiki pages from one source

## Founding Ask Coverage

`founding_ask_ref`: Tier 3. `founding_ask_ref_sha256`
`cc266ebe307ece299aea75e1f3ed34acc3428467545b0a3e654850de2fc4bfca`, record content sha256
`7e40d47e4e7a8cc4b38f1dc02d435f916c11926c3d59ef6f66cc4552708b8df9`, entry `A001`, generation 1.
The record carries the owner's words verbatim with a pasted terminal transcript naming a private
host, so it is kept in the operator's private configuration repository and not reproduced here.
This index is non-authoritative; the raw record governs.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| A001: why `ai -h` does not show full command descriptions | [§ 1](#1-why-ai--h-truncates), measured against the installed Click |
| A001: whether every option has both short and long names | [§ 1.3](#13-options-and-their-two-forms): all 227 visible options do; three self-parsing commands hide theirs |
| A001: one shared source generating both the help and the canonical CLI usage doc, enforced aligned | [§ 2](#2-generators-that-read-click), [§ 3](#3-drift-checks), [Recommendation](#recommendation) |
| A001: the wiki, "everything about this repo", from a single source where possible | [§ 5](#5-github-wiki-mechanics-and-publishing) |
| A001: auto-generate "as programmatically as possible", LLM allowed | [§ 4](#4-agentic-and-llm-documentation-generation), [§ 4.4](#44-the-seam-between-derived-facts-and-llm-prose) |
| A001: survey open-source packages, tools and repos for generated documentation | [§ 2](#2-generators-that-read-click), [§ 4.1](#41-agentic-documentation-tools), [§ 5.2](#52-wiki-publishers) |
| A001: survey existing documentation on programmatic and agentic documentation generation | [§ 4.2](#42-published-evidence-on-quality-and-accuracy) and [§ 4.3](#43-mechanisms-that-keep-llm-prose-honest) |
| A001: a hosted website later | Deferred by the ask; compatibility notes in [§ 6](#6-feeding-a-later-docs-website) |
| A001: Beads to GitHub issues and Projects sync; GitHub Discussions | Separate epics in the ask, filed by the launching session in the operator's private task store; not researched here |

**Status:** complete

**Created:** 2026-11-11

<!-- doc:region name="context" kind="immutable" -->

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Context](#context)
- [Executive Summary](#executive-summary)
- [1. Why `ai -h` truncates](#1-why-ai--h-truncates)
  - [1.1 The mechanism](#11-the-mechanism)
  - [1.2 The controls](#12-the-controls)
  - [1.3 Options and their two forms](#13-options-and-their-two-forms)
  - [1.4 Coverage of the hand-written docs](#14-coverage-of-the-hand-written-docs)
- [2. Generators that read Click](#2-generators-that-read-click)
- [3. Drift checks](#3-drift-checks)
- [4. Agentic and LLM documentation generation](#4-agentic-and-llm-documentation-generation)
  - [4.1 Agentic documentation tools](#41-agentic-documentation-tools)
  - [4.2 Published evidence on quality and accuracy](#42-published-evidence-on-quality-and-accuracy)
  - [4.3 Mechanisms that keep LLM prose honest](#43-mechanisms-that-keep-llm-prose-honest)
  - [4.4 The seam between derived facts and LLM prose](#44-the-seam-between-derived-facts-and-llm-prose)
- [5. GitHub wiki mechanics and publishing](#5-github-wiki-mechanics-and-publishing)
  - [5.1 Platform facts](#51-platform-facts)
  - [5.2 Wiki publishers](#52-wiki-publishers)
- [6. Feeding a later docs website](#6-feeding-a-later-docs-website)
- [Gaps, blindspots and emergent findings](#gaps-blindspots-and-emergent-findings)
- [Recommendation](#recommendation)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

## Context

The `ai` command (package `ai-cli-utils`) is a Click 8.5.0 application with 52 visible top-level
commands and 96 visible commands in all. Its owner asked why `ai -h` cuts command descriptions
short, whether every option shows both its short and long name, and for one shared source that
generates the help, the canonical CLI usage document and the project's GitHub wiki, as
programmatically as possible, with an LLM only where prose cannot be derived. The owner also asked
for a survey of open-source tooling and of published work on programmatic and agentic documentation
generation. A hosted website is wanted later and is out of scope here.

Two read-only research legs gathered the external evidence; the coordinator measured the repository
and the installed Click directly and wrote this synthesis. Measurements were taken on 2026-11-11
against `origin/main` at `d301960c`.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Executive Summary

The truncation is Click's default: each row in a command list is the first sentence of the
command's help, shortened to fit the column, and no `ai` command sets the one field Click prints
unshortened. One override of the group class's `format_commands` removes it for every command.
Every one of the 227 visible options already has both forms; `ai -h` shows only the root's options
by design, and three commands parse their own arguments so their flags appear nowhere.

No maintained, standalone "Click to Markdown with a check mode" tool exists. The pattern that
mature Click projects use is a short tree walk over Click's own help records that writes Markdown
and fails CI when the committed file differs, and that is the recommendation here: an in-repo
generator with no new dependency, so the help screen, the reference document and the wiki all
come from the decorators. For the wiki, GitHub requires one page to be created by hand before any
push works, and the maintained publisher defaults to the workflow token; a plain checkout-and-push
step does the same without third-party code.

LLM and agent tools are mostly hosted wikis that cannot be the canonical source, and the accuracy
evidence is thin: an ungrounded LLM misnamed about 39% of repository-specific entities in the one
entity-level study. The evidence supports keeping LLMs out of anything derivable and using them to
draft narrative prose that is then reviewed and committed.

## 1. Why `ai -h` truncates

### 1.1 The mechanism

`Group.format_commands` computes `limit = formatter.width - 6 - max(len(name))` and fills each row
with `command.get_short_help_str(limit)`. [VERIFIABLE, measured in the installed Click 8.5.0
source] `get_short_help_str` returns `short_help` verbatim when it is set, and otherwise calls the
private `_make_default_short_help(help, limit)`, which takes the first sentence of the first
paragraph and drops words until the text plus "..." fits. [VERIFIABLE, measured] Click's own
documentation describes the same behaviour and the `short_help` override. [VERIFIABLE][^1]

The formatter's width is `max(min(terminal_columns, max_content_width) - 2, 50)` with
`max_content_width` defaulting to 80, so on any terminal at least 80 columns wide the width is 78.
With the longest visible name, `clear-restart-request`, at 21 characters, the limit is
78 - 6 - 21 = 51 characters. [VERIFIABLE, measured: `ctx.make_formatter().width == 78`] Click's
`CliRunner` forces width 80, so help captured in tests is two columns wider than a user's terminal
and a golden file captured that way never matches the screen exactly. [VERIFIABLE, measured]

No command in the tree sets `short_help`, and no `max_content_width` or `terminal_width` is set
anywhere. [VERIFIABLE, measured by tree walk and grep]

### 1.2 The controls

| Control | Effect | Limitation |
|---|---|---|
| `short_help=` on a command | Printed in full; `write_dl` wraps it | One string per command, 96 of them, and a new command without one regresses |
| Override `format_commands` on a shared group class | Every row printed from an untruncated summary | Every group must use the class |
| `context_settings={"max_content_width": N}` on the root | Wider rendering on wider terminals | No effect on an 80-column terminal |
| A custom `formatter_class` | Full control of all help output | Changes every help screen |

[VERIFIABLE, measured with `max_content_width=200, terminal_width=200`: the `add-name` row prints in
full] Measured first-sentence lengths across the 96 visible commands: median 42 characters, 90th
percentile 87, maximum 133, so untruncated rows wrap to at most three lines at width 78.
[VERIFIABLE, measured]

Two generator constraints come out of the source. `Command.help` still holds any text after a `\f`
marker, which Click hides only when formatting, so a generator that reads `command.help` directly
publishes text the help screen hides. [VERIFIABLE, measured] And `click.utils.make_default_short_help`
is a deprecated alias that warns it will be removed in Click 9.0, so a design must not call it.
[VERIFIABLE, measured]

### 1.3 Options and their two forms

All 227 visible options in the tree have both a short and a long form, and
`tests/test_cli_option_forms.py` enforces it. [VERIFIABLE, measured] `ai -h` lists only the root
command's own options; each command's options appear under `ai <command> -h`. That is Click's
design, not an omission.

Three commands are outside that count. `ai ps`, `ai layout` and `ai sync` take all arguments as
unprocessed text with `help_option_names` set to none, and parse actions and flags by hand
(`ps clean|cron -f -r`; `layout list|validate|profiles`; `sync push|pull|conflicts|resolve|watch|
repair-worktree|cleanup -n -v`). [VERIFIABLE, measured in `src/ai_cli/main.py` and
`src/ai_cli/process_hygiene.py`] Their flags reach no help screen. `ai ps -h` ignores the flag and
runs the process listing, and `ai sync -h` fails with "Unknown sync action: -h". [VERIFIABLE,
measured 2026-11-11] Nine visible `ai sessions` subcommands have no help text at all.
[VERIFIABLE, measured]

### 1.4 Coverage of the hand-written docs

Of the 52 visible top-level commands, 24 never appear as `ai <name>` in
`docs/tools/ai-cli-usage.md`, 9 never appear in the README, and four (`backfill-trust`,
`copier-update`, `deploy`, `session-reaper`) appear in neither. [VERIFIABLE, measured with a
word-boundary match on `ai <name>`] The usage doc also carries hand-copied flag tables. Nothing
checks either document against the code.

Help text is static: no help string or shown default reads configuration or the environment at
import, so a generated reference can be byte-identical across machines. [VERIFIABLE, measured by
grep over `help=f"...`, computed defaults and `show_default`]

## 2. Generators that read Click

| Tool | What it produces | Maintenance (2026-11-11) | Fit here |
|---|---|---|---|
| mkdocs-click | Markdown for a command tree inside MkDocs; `table` style shows both option names | 0.9.0, 2025-04-07; Apache-2.0; 152 stars [VERIFIABLE][^2][^3] | The reusable generator is the private module `mkdocs_click._docs`; its anchors need an extension the wiki lacks [VERIFIABLE][^4] |
| sphinx-click | Sphinx `program`/`option` directives | 6.2.0, 2025-12-04; MIT [VERIFIABLE][^5][^6] | Requires Sphinx and reStructuredText; not Markdown in the repo |
| click-man | One man page per command | 0.6.0, 2026-10-15; MIT [VERIFIABLE][^7] | Man pages only; no Windows story |
| md-click | Markdown files | 1.0.1, 2021; pinned `click<=7.1.2`; archived [VERIFIABLE][^8] | Cannot install against Click 8.5 |
| click-extra | Drop-in Click extension; `--help-format markdown-full` renders the tree | 10.2.0, 2026-11-06, four releases in one week; GPL-2.0-or-later [VERIFIABLE][^9][^10] | Copyleft licence for an MIT package, and a framework migration; dev-only use of its `wrap` mode is unmeasured |
| Typer `get_docs_for_click` | Markdown bullets built from Click's `get_help_record` | typer 0.29.0, 2026-11-09; MIT [VERIFIABLE][^11][^12] | Not a documented API; adds a second CLI framework |
| cog (cogapp) | Regenerates text between markers by running embedded Python; `--check`, `--diff`, `-c` checksum | 3.6.0, 2025-09-21; MIT; 408 stars [VERIFIABLE][^13][^14] | An embedding mechanism, not a documenter; the tree walk still has to be written |

The pattern in production is the tree walk itself. The `llm` CLI (12,745 stars, pushed 2026-11-06)
keeps its full CLI reference in `docs/help.md`, generated by a cog block that walks the Click tree,
and its CI runs `cog --check` on the docs. [VERIFIABLE][^15][^16] mkdocs-click and Typer implement
the same walk independently: each command's help records come from Click's own
`get_help_record`, so both option names appear exactly as `-h` prints them. [VERIFIABLE][^4][^12]

No maintained, standalone "Click to Markdown with a check mode" CLI was found. [NO SOURCE] That gap
is why an in-repo generator over Click's public API comes out ahead: it serves the repository file,
a README region and the wiki from one walk, with no new runtime dependency. [INFERENCE] A separate
data file (TOML or YAML) rendering both help and docs has no maintained tool behind it for Click
and would duplicate 227 option declarations that must still be wired to callbacks. [INFERENCE]

## 3. Drift checks

- A generator with a `--check` mode that diffs a fresh render against the committed file is the
  standard drift check; cog's `--check` with `--diff` is the reference implementation. [VERIFIABLE][^14]
- The repository already ships one such check, `scripts/lint_doc_alignment.py`, which exits 1 with
  a diff when two context files drift. [VERIFIABLE, measured]
- Snapshot libraries exist for the help screen itself: syrupy 7.0.0 (2026-11-11, MIT) and
  inline-snapshot 0.36.2 (2026-11-09, MIT). [VERIFIABLE][^17][^18] For this project the generated
  reference is already the snapshot, so a plugin adds value only for a pinned-width `ai -h`
  assertion. [INFERENCE]
- Docs as tests: Doc Detective executes actions found in docs (AGPL-3.0, Node). [VERIFIABLE][^19]
  For a CLI, the cheaper equivalent is a pytest that reads every `ai ...` example in the
  hand-written docs and checks that the command path and options exist. [INFERENCE]
- Link checking with lychee (Apache-2.0, 4,120 stars) and prose linting with Vale (MIT, 6,372 stars)
  run without an LLM. [VERIFIABLE][^20][^21]
- Line endings: a generator that writes platform line endings fails a check run on Windows. This
  repository's `.gitattributes` already forces `eol=lf` on every checkout, so the generator must
  also write LF explicitly. [VERIFIABLE, measured]

## 4. Agentic and LLM documentation generation

### 4.1 Agentic documentation tools

| Tool | Output | Runs headless in CI | Provider | Maintenance | Fit |
|---|---|---|---|---|---|
| RepoAgent | Per-object Markdown for Python code, committed | Pre-commit flow; `repoagent diff` scopes regeneration | Any OpenAI-compatible endpoint | Apache-2.0, 1,061 stars, last push 2024-12-23 [VERIFIABLE][^22][^23] | Dormant; documents code objects, not a CLI |
| DocAgent | Python docstrings via five agents | Research code | OpenAI and others | MIT, 457 stars, last push 2025-04-23 [VERIFIABLE][^24] | Research release |
| readme-ai | One README file | CLI and Docker | OpenAI, Anthropic, Gemini, Ollama, or `offline` templates | MIT, 3,008 stars, active [VERIFIABLE][^25] | One-shot scaffolding; overwrites rather than maintains |
| deepwiki-open | Hosted wiki app | No CI mode documented | Google, OpenAI, Ollama, LiteLLM | MIT, 18,401 stars; rebranded mid-life [VERIFIABLE][^26] | A server, not committed docs |
| OpenDeepWiki | Hosted wiki platform | Scheduled background updates | Any OpenAI-style endpoint | MIT, 3,662 stars [VERIFIABLE][^27] | A server, not committed docs |
| DeepWiki (Cognition) | Hosted wiki, free for public repos; steerable by a committed `.devin/wiki.json` (max 30 pages) | Hosted only | Cognition only | Commercial [VERIFIABLE][^28] | Optional external mirror, never canonical |
| Google Code Wiki | Hosted wiki regenerated after each change | Hosted only | Gemini only | Announced 2025-11-13 [VERIFIABLE][^29] | Same class as DeepWiki |
| Mintlify agent and automations | Doc changes as pull requests; "code change" trigger | Enterprise tier for automations | Hosted | Paid [VERIFIABLE][^30][^31] | Right pattern, wrong cost for a one-maintainer project |
| claude-code-action | Claude Code in a workflow; headless on `schedule` or `push`; commits and opens PRs | Yes | Claude only (API, Bedrock, Vertex, Foundry) | MIT, 9,881 stars, active [VERIFIABLE][^32][^33] | Direct route to "after a merge, open a doc-update PR" |
| GitHub Agentic Workflows (gh-aw) | Markdown workflows compiled to Actions; agents read-only, writes through "safe outputs" | Yes | Copilot, Claude Code, Codex and others | MIT, 5,613 stars, technical preview [VERIFIABLE][^34][^35] | Ships `update-docs` (draft PRs) and an "Agentic Wiki Writer" |
| CodeWiki | Markdown in `./docs/`, one page per module; `--update` regenerates only changed modules | Yes | Any OpenAI-compatible endpoint, Anthropic, Bedrock, others | MIT, 1,853 stars, pushed 2026-11-09 [VERIFIABLE][^36][^37] | The only maintained, provider-neutral, change-scoped, committed-Markdown generator found; architecture prose, not CLI reference |

The Agentic Wiki Writer in the gh-aw catalogue builds wiki pages from a committed
`.github/agentic-wiki/PAGES.md` page plan, where `*{ ... }*` marks the regions the LLM writes, and
pushes straight to the wiki repository with no pull request in between. [VERIFIABLE][^38] Its
`update-docs` sibling opens draft PRs only. [VERIFIABLE][^39]

Every maintained agentic doc tool checked defaults to a pull request as the review point, except the
Agentic Wiki Writer. [VERIFIABLE][^30][^33][^34][^38][^39]

### 4.2 Published evidence on quality and accuracy

- DocAgent measured truthfulness as the share of repository-specific entities a generated
  docstring mentions that really exist. Plain chat with GPT-4o mini scored 61.10%; the grounded
  multi-agent system scored 95.74%, on 366 components from 9 repositories. [VERIFIABLE][^40] The
  paper's own counts do not reproduce its percentages (265/305 is 86.9%), so the figures are
  indicative, not precise. [VERIFIABLE][^40]
- CodeWiki (ACL 2026) scored 68.79% against DeepWiki's 64.06% on an LLM-judged rubric benchmark,
  which is a quality score, not an error rate. [VERIFIABLE][^41]
- SWD-Bench (2026) argues LLM-as-judge scoring of documentation is unreliable and measures instead
  whether docs help an agent complete tasks; the best method raised issue-solving by 20%.
  [VERIFIABLE][^42]
- A README-generation study (ICSME 2026) found a single-agent pipeline matched a multi-agent one at
  86% fewer tokens, and a lightweight developer-written plan gave the best quality. [VERIFIABLE][^43]
- Across 29,669 agent-authored pull requests in 2,585 repositories, 63.2% changed code with no
  documentation update. [VERIFIABLE][^44]
- In a single-repository case study, one LLM audit pass found about half of 47 known stale
  statements, with at least 93% of its findings correct. [VERIFIABLE][^45]
- ReCite (ASE 2026) detects stale function references in kernel comments by symbol resolution and
  asks an LLM only for the repair; 50 of 75 submitted patches were accepted. [VERIFIABLE][^46]
- No study measured a factual-error rate for whole-repository wiki generators (DeepWiki, Code Wiki,
  deepwiki-open, OpenDeepWiki). [NO SOURCE]

### 4.3 Mechanisms that keep LLM prose honest

- Generated regions the LLM may not edit: cog's checksum mode refuses to overwrite a hand-edited
  generated region, and `--check` fails CI. [VERIFIABLE][^14]
- A committed page plan that marks LLM-written regions (`PAGES.md`) or fixes the page list
  (`.devin/wiki.json`). [VERIFIABLE][^28][^38]
- Review gates: draft pull requests from a read-only agent, with writes applied by a separate job
  (gh-aw safe outputs). [VERIFIABLE][^34]
- Regeneration only on source change: CodeWiki `--update`, RepoAgent `diff`, and the wiki writer's
  skip when nothing merged. [VERIFIABLE][^22][^36][^38]
- Advisory LLM consistency checks such as semcheck (MIT, 114 stars), which maps code to doc files
  and asks a model whether they agree. [VERIFIABLE][^47] At the roughly 50% recall measured above,
  such a check is a reviewer aid, not a merge gate. [INFERENCE]
- A per-page "generated by model X from commit Y" provenance convention: none published. [NO SOURCE]

### 4.4 The seam between derived facts and LLM prose

The evidence converges on one split. Anything the code can produce (command lists, usage lines,
options, defaults) comes from the code, because an ungrounded LLM misnames a large share of such
facts. [INFERENCE from § 4.2] Narrative prose (overviews, concepts, how-tos) has no structured
source; an LLM can draft it against a fixed page plan, and the draft lands in a reviewed pull
request as an ordinary committed file. [INFERENCE from § 4.2 and § 4.3] A post-merge job that opens
a draft doc-update PR (claude-code-action or a gh-aw workflow) is the next step if prose upkeep
becomes a burden, and it only makes sense once the generated reference and drift checks exist for
it to anchor to. [INFERENCE]

Two risks shape any LLM job in this public repository. Pages fetched during this research carried
instructions addressed to AI agents, so a doc agent that reads untrusted content while holding a
write token is exposed; a read-only agent whose output is a pull request is the mitigation the
evidence supports. [VERIFIABLE][^30][^34] And fork pull requests get no secrets, while scheduled
workflows in public repositories are disabled after 60 days without activity, so such a job runs
post-merge on the default branch. [VERIFIABLE][^33]

## 5. GitHub wiki mechanics and publishing

### 5.1 Platform facts

- A wiki is the git repository `<repo>.wiki.git`, clonable only "once you've created an initial
  page on GitHub". The filename is the page title, the extension picks the renderer, and only the
  default branch goes live. [VERIFIABLE][^48]
- This repository's wiki is enabled (`has_wiki: true` in the repository API) but `git ls-remote` on
  its `.wiki.git` answers "Repository not found", so the first page has not been created.
  [VERIFIABLE, measured 2026-11-11 by the coordinator]
- `_Sidebar.md` and `_Footer.md` populate the sidebar and footer. [VERIFIABLE][^49] Markdown links
  between pages use the page URL; transclusion and tables of contents are not supported.
  [VERIFIABLE][^50]
- Search engines index a wiki only when the repository has 500 or more stars and public editing is
  blocked, and GitHub recommends Pages when indexing matters. [VERIFIABLE][^51] This repository has
  one star, so its wiki is a reading surface for people who already found the repository, and the
  canonical text should stay in the repository. [INFERENCE]
- A wiki has no pull-request flow of its own; anything pushed is live. [VERIFIABLE][^48] So any
  text that reaches the wiki should be reviewed in the main repository first and copied by a
  deterministic job. [INFERENCE]
- The workflow `permissions` list has no wiki scope. [VERIFIABLE][^52] The most-used maintained
  publisher defaults its token to `${{ github.token }}` with `contents: write` and needs a personal
  token only for another repository's wiki. [VERIFIABLE][^53] A community report confirms pushing to
  `${{ github.repository }}.wiki` with `actions/checkout` and the default token. [VERIFIABLE][^54]
  One smaller action says the default token does not work. [VERIFIABLE][^55] The first real run
  settles it for this repository. [INFERENCE]

### 5.2 Wiki publishers

| Tool | State | Token | Notes |
|---|---|---|---|
| Andrew-Chen-Wang/github-wiki-action | v5.0.6, 2026-07-11; Apache-2.0; 109 stars [VERIFIABLE][^56] | Default workflow token | Full-replace sync; preprocessing renames `README.md` to `Home.md`, strips `.md` from links and flattens cross-directory links to basenames; runs a Deno script [VERIFIABLE][^57] |
| newrelic/wiki-sync-action | Last push 2024-08-27; 33 stars [VERIFIABLE][^58] | Personal token required | Two-way |
| cmbrose/github-docs-to-wiki | Last push 2026-04-23; 11 stars [VERIFIABLE][^55] | Personal token required | Rewrites links, page names from headers |
| SwiftDocOrg/github-wiki-publish-action | Archived [VERIFIABLE][^59] | n/a | Reject |
| `actions/checkout` of `<repo>.wiki` plus `git push` | First-party | Default workflow token | No third-party code [VERIFIABLE][^54] |

Full-replace publishing overwrites any edit made in the wiki's web editor on the next run.
[VERIFIABLE][^57]

## 6. Feeding a later docs website

- MkDocs 1.6.1 (2024-08-30) is still the latest stable release; 2.0 is in pre-release.
  [VERIFIABLE][^60] Material for MkDocs entered maintenance mode, with fixes until 2027-05-05, and
  its team now builds Zensical, an MIT generator that reads `mkdocs.yml`. [VERIFIABLE][^61]
- Zensical 0.1.1 (2026-11-10, MIT) states that the most popular third-party Markdown extensions work
  unchanged. [VERIFIABLE][^62] Whether mkdocs-click runs under it is not stated. [NO SOURCE]
- Starlight (MIT, 9,547 stars) and Docusaurus (MIT, 66,704 stars) build sites from Markdown or MDX.
  [VERIFIABLE][^63][^64]
- llms.txt is a proposed Markdown index of a docs site plus `.md` copies of its pages; it is fully
  derivable from the docs tree. [VERIFIABLE][^65]

Plain committed Markdown under `docs/` is the input every one of these accepts, so a generator that
writes ordinary Markdown files feeds the repository, the wiki and a later site without a rewrite.
Depending on an MkDocs-only directive now would bind the reference to a tool whose stable line has
not shipped since 2024. [INFERENCE]

## Gaps, blindspots and emergent findings

- The width figure in the original framing (80, giving about 53) is the `CliRunner` number; a real
  terminal gets 78 and 51. Any generator or golden test must pin the width. [VERIFIABLE, measured]
- The three self-parsing commands (§ 1.3) were not in the original question and turn "are these all
  the options" into a real gap: their options are in no help screen and no document. [VERIFIABLE,
  measured]
- The wiki's search invisibility below 500 stars and its lack of a review path both came from
  outside the anchor list and argue for keeping canonical text in the repository with the wiki as a
  generated mirror. [VERIFIABLE][^51][^48]
- Coding agents add documentation drift by default (63.2% of agent PRs), so a repository developed
  mostly through agents needs mechanical drift checks whatever generates its docs. [VERIFIABLE][^44]
- The accuracy evidence is thin and partly inconsistent. Any LLM adoption should assume errors at an
  unknown rate and rely on review and mechanical checks. [INFERENCE]
- General web search was unavailable on this host (native search is not served and the fallback
  search page served a bot challenge), so coverage relied on primary documentation, package indexes,
  the GitHub API and arXiv. The host's sanctioned search service was not loadable from the
  research sub-agents or the coordinator. A tool discoverable only by search may be missing; the
  one such gap that touches the design (a standalone Click-to-Markdown tool with a check mode) does
  not change the recommendation, which rests on avoiding a dependency and a private API. [NO SOURCE]
- Not measured: click-extra's `wrap --help-format markdown-full` output on this CLI, CodeWiki's
  output on this repository (no repository content was sent to any service), and whether any search
  engine reads llms.txt. [NO SOURCE]

## Recommendation

1. Fix the truncation with one shared `click.Group` subclass whose `format_commands` prints each
   command's summary untruncated, computed by one function that the document generator also calls.
   Raise `max_content_width` to 100 for wider terminals.
2. Declare the actions and options of `ai ps`, `ai layout` and `ai sync` in Click, so their flags
   enter the single source and their `-h` works.
3. Write a small in-repo generator over Click's public help API (`get_help_record`, usage pieces,
   Click's own help-text formatting) at a pinned width with LF output. It writes a fully generated
   `docs/reference/cli.md` and a marked command-index region in the README, and has a `--check`
   mode. Run the comparison in pytest on all three CI systems and as a pre-commit hook.
4. Add a pytest that checks every `ai ...` example in the hand-written docs against the command
   tree.
5. Build the wiki from a committed manifest over the repository's own Markdown, with link
   rewriting and a generated sidebar and footer, in CI on every push to `main`, and push it with a
   first-party `actions/checkout` step and the workflow token. Create the first wiki page by hand
   once.
6. Keep every LLM out of generation and CI. Use one to draft narrative prose or missing help text
   in a reviewed pull request. Consider a post-merge draft-PR doc job, or CodeWiki for architecture
   pages, only after the generated reference and checks are in place.

## Open Questions

None. The one platform fact not settled by documentation, whether the default workflow token can
push to this repository's wiki, is settled by the first workflow run after the first page exists;
if it cannot, a fine-grained personal token scoped to this repository is the documented fallback.

## Sources

[^1]: Pallets. (2026). [Click: documenting scripts](https://click.palletsprojects.com/en/stable/documentation/). Click documentation. Verified accessible (HTTP 200) 2026-11-11.
[^2]: mkdocs. (2025). [mkdocs-click README](https://github.com/mkdocs/mkdocs-click). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^3]: Python Package Index. (2025). [mkdocs-click JSON metadata](https://pypi.org/pypi/mkdocs-click/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^4]: mkdocs. (2025). [mkdocs_click/_docs.py](https://raw.githubusercontent.com/mkdocs/mkdocs-click/master/mkdocs_click/_docs.py). GitHub source. Verified accessible (HTTP 200) 2026-11-11.
[^5]: click-contrib. (2025). [sphinx-click usage](https://sphinx-click.readthedocs.io/en/latest/usage/). Read the Docs. Verified accessible (HTTP 200) 2026-11-11.
[^6]: Python Package Index. (2025). [sphinx-click JSON metadata](https://pypi.org/pypi/sphinx-click/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^7]: Python Package Index. (2026). [click-man JSON metadata](https://pypi.org/pypi/click-man/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^8]: Python Package Index. (2021). [md-click JSON metadata](https://pypi.org/pypi/md-click/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^9]: Deldycke, K. (2026). [Click Extra: machine-readable help](https://kdeldycke.github.io/click-extra/machine-readable.html). Verified accessible (HTTP 200) 2026-11-11.
[^10]: Python Package Index. (2026). [click-extra JSON metadata](https://pypi.org/pypi/click-extra/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^11]: Python Package Index. (2026). [typer JSON metadata](https://pypi.org/pypi/typer/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^12]: FastAPI organization. (2026). [typer/cli.py](https://raw.githubusercontent.com/fastapi/typer/master/typer/cli.py). GitHub source. Verified accessible (HTTP 200) 2026-11-11.
[^13]: Python Package Index. (2025). [cogapp JSON metadata](https://pypi.org/pypi/cogapp/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^14]: Batchelder, N. (2026). [Running cog](https://cog.readthedocs.io/en/latest/running.html). Read the Docs. Verified accessible (HTTP 200) 2026-11-11.
[^15]: Willison, S. (2026). [llm docs/help.md](https://raw.githubusercontent.com/simonw/llm/main/docs/help.md). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^16]: Willison, S. (2026). [llm test workflow](https://raw.githubusercontent.com/simonw/llm/main/.github/workflows/test.yml). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^17]: Python Package Index. (2026). [syrupy JSON metadata](https://pypi.org/pypi/syrupy/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^18]: Python Package Index. (2026). [inline-snapshot JSON metadata](https://pypi.org/pypi/inline-snapshot/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^19]: Doc Detective. (2026). [Doc Detective README](https://github.com/doc-detective/doc-detective). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^20]: GitHub REST API. (2026). [lycheeverse/lychee repository metadata](https://api.github.com/repos/lycheeverse/lychee). Verified accessible (HTTP 200) 2026-11-11.
[^21]: GitHub REST API. (2026). [errata-ai/vale repository metadata](https://api.github.com/repos/errata-ai/vale). Verified accessible (HTTP 200) 2026-11-11.
[^22]: OpenBMB. (2024). [RepoAgent README](https://github.com/OpenBMB/RepoAgent). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^23]: GitHub REST API. (2026). [OpenBMB/RepoAgent repository metadata](https://api.github.com/repos/OpenBMB/RepoAgent). Verified accessible (HTTP 200) 2026-11-11.
[^24]: GitHub REST API. (2026). [facebookresearch/DocAgent repository metadata](https://api.github.com/repos/facebookresearch/DocAgent). Verified accessible (HTTP 200) 2026-11-11.
[^25]: eli64s. (2026). [readme-ai README](https://github.com/eli64s/readme-ai). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^26]: AsyncFuncAI. (2026). [deepwiki-open README.md](https://raw.githubusercontent.com/AsyncFuncAI/deepwiki-open/main/README.md). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^27]: AIDotNet. (2026). [OpenDeepWiki README](https://github.com/AIDotNet/OpenDeepWiki). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^28]: Cognition. (2026). [DeepWiki](https://docs.devin.ai/work-with-devin/deepwiki). Devin documentation. Verified accessible (HTTP 200) 2026-11-11.
[^29]: Google. (2025, November 13). [Introducing Code Wiki](https://developers.googleblog.com/en/introducing-code-wiki-accelerating-your-code-understanding/). Google Developers Blog. Verified accessible (HTTP 200) 2026-11-11.
[^30]: Mintlify. (2026). [Agent](https://www.mintlify.com/docs/ai/agent). Mintlify Docs. Verified accessible (HTTP 200) 2026-11-11. (The page carried instructions addressed to AI agents, which were not followed.)
[^31]: Mintlify. (2026). [Workflows (automations)](https://www.mintlify.com/docs/agent/workflows). Mintlify Docs. Verified accessible (HTTP 200) 2026-11-11.
[^32]: GitHub REST API. (2026). [anthropics/claude-code-action repository metadata](https://api.github.com/repos/anthropics/claude-code-action). Verified accessible (HTTP 200) 2026-11-11.
[^33]: Anthropic. (2026). [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions). Claude Code Docs. Verified accessible (HTTP 200) 2026-11-11.
[^34]: GitHub. (2026). [GitHub Agentic Workflows](https://github.github.io/gh-aw/). Verified accessible (HTTP 200) 2026-11-11.
[^35]: GitHub REST API. (2026). [github/gh-aw repository metadata](https://api.github.com/repos/github/gh-aw). Verified accessible (HTTP 200) 2026-11-11.
[^36]: FSoft-AI4Code. (2026). [CodeWiki README](https://github.com/FSoft-AI4Code/CodeWiki). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^37]: GitHub REST API. (2026). [FSoft-AI4Code/CodeWiki repository metadata](https://api.github.com/repos/FSoft-AI4Code/CodeWiki). Verified accessible (HTTP 200) 2026-11-11.
[^38]: GitHub Next. (2026). [agentic-wiki-writer.md workflow](https://raw.githubusercontent.com/githubnext/agentics/main/workflows/agentic-wiki-writer.md). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^39]: GitHub Next. (2026). [update-docs.md workflow](https://raw.githubusercontent.com/githubnext/agentics/main/workflows/update-docs.md). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^40]: Yang, D., Simoulin, A., Qian, X., et al. (2025). [DocAgent, full text v3](https://arxiv.org/html/2504.08725v3). arXiv. Verified accessible (HTTP 200) 2026-11-11.
[^41]: Nguyen Hoang, A., Le-Anh, M., Le, B., & Bui, N. D. Q. (2025, rev. 2026). [CodeWiki: Evaluating AI's ability to generate holistic documentation for large-scale codebases](https://arxiv.org/abs/2510.24428). arXiv:2510.24428. Verified accessible (HTTP 200) 2026-11-11.
[^42]: Wang, X., Hu, R., Gao, C., Gao, P., & Peng, C. (2026). [Evaluating repository-level software documentation via question answering and feature-driven development](https://arxiv.org/abs/2604.06793). arXiv:2604.06793. Verified accessible (HTTP 200) 2026-11-11.
[^43]: Saleh, A., Tesfay, T. W., Nguyen, P. T., Di Rocco, J., Zeshan, M. U., & Di Ruscio, D. (2026). [The illusion of agentic complexity in README.md generation](https://arxiv.org/abs/2606.30524). arXiv:2606.30524. Verified accessible (HTTP 200) 2026-11-11.
[^44]: De Michele, W., & Tantithamthavorn, K. (2026). [Documentation drift in AI-generated pull requests](https://arxiv.org/abs/2610.27381). arXiv:2610.27381. Verified accessible (HTTP 200) 2026-11-11.
[^45]: Hassanzadeh Aliabadi, H. (2026). [Lessons outlive their failures: A case study of rules for AI coding agents](https://arxiv.org/abs/2610.32578). arXiv:2610.32578. Verified accessible (HTTP 200) 2026-11-11.
[^46]: Sun, K., Lyu, Y., Ma, X., et al. (2026). [We must have missed this comment: Detecting and repairing stale function references in Linux kernel comments](https://arxiv.org/abs/2608.03734). arXiv:2608.03734. Verified accessible (HTTP 200) 2026-11-11.
[^47]: ReJot. (2026). [semcheck README](https://github.com/rejot-dev/semcheck). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^48]: GitHub. (2026). [Adding or editing wiki pages](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages). GitHub Docs. Verified accessible (HTTP 200) 2026-11-11.
[^49]: GitHub. (2026). [Creating a footer or sidebar for your wiki](https://docs.github.com/en/communities/documenting-your-project-with-wikis/creating-a-footer-or-sidebar-for-your-wiki). GitHub Docs. Verified accessible (HTTP 200) 2026-11-11.
[^50]: GitHub. (2026). [Editing wiki content](https://docs.github.com/en/communities/documenting-your-project-with-wikis/editing-wiki-content). GitHub Docs. Verified accessible (HTTP 200) 2026-11-11.
[^51]: GitHub. (2026). [About wikis](https://docs.github.com/en/communities/documenting-your-project-with-wikis/about-wikis). GitHub Docs. Verified accessible (HTTP 200) 2026-11-11.
[^52]: GitHub. (2026). [Workflow syntax for GitHub Actions: permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax). GitHub Docs. Verified accessible (HTTP 200) 2026-11-11.
[^53]: Chen-Wang, A. (2026). [github-wiki-action README](https://github.com/Andrew-Chen-Wang/github-wiki-action). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^54]: GitHub Community. (2020). [Discussion #25929: pushing to a wiki from Actions](https://github.com/orgs/community/discussions/25929). GitHub. Verified accessible (HTTP 200) 2026-11-11. (Community answer, not identified as staff.)
[^55]: Brose, C. (2026). [github-docs-to-wiki README](https://github.com/cmbrose/github-docs-to-wiki). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^56]: GitHub REST API. (2026). [github-wiki-action latest release](https://api.github.com/repos/Andrew-Chen-Wang/github-wiki-action/releases/latest). Verified accessible (HTTP 200) 2026-11-11.
[^57]: Chen-Wang, A. (2026). [github-wiki-action cli.ts](https://raw.githubusercontent.com/Andrew-Chen-Wang/github-wiki-action/master/cli.ts). GitHub source. Verified accessible (HTTP 200) 2026-11-11.
[^58]: New Relic. (2024). [wiki-sync-action README](https://github.com/newrelic/wiki-sync-action). GitHub. Verified accessible (HTTP 200) 2026-11-11.
[^59]: GitHub REST API. (2021). [SwiftDocOrg/github-wiki-publish-action repository metadata](https://api.github.com/repos/SwiftDocOrg/github-wiki-publish-action). Verified accessible (HTTP 200) 2026-11-11.
[^60]: Python Package Index. (2026). [mkdocs JSON metadata](https://pypi.org/pypi/mkdocs/json). PyPI. Verified accessible (HTTP 200) 2026-11-11.
[^61]: squidfunk. (2025). [Zensical announcement](https://squidfunk.github.io/mkdocs-material/blog/2025/11/05/zensical/). Material for MkDocs blog. Verified accessible (HTTP 200) 2026-11-11.
[^62]: Zensical. (2026). [Compatibility](https://zensical.org/compatibility/). Verified accessible (HTTP 200) 2026-11-11.
[^63]: GitHub REST API. (2026). [withastro/starlight repository metadata](https://api.github.com/repos/withastro/starlight). Verified accessible (HTTP 200) 2026-11-11.
[^64]: GitHub REST API. (2026). [facebook/docusaurus repository metadata](https://api.github.com/repos/facebook/docusaurus). Verified accessible (HTTP 200) 2026-11-11.
[^65]: Howard, J. (2024, rev. 2026). [The /llms.txt file](https://llmstxt.org/). llmstxt.org. Verified accessible (HTTP 200) 2026-11-11.

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** none (no research registry on this host)
**Model:** Claude Opus 5.5 (both legs and the synthesis)
**Date:** 2026-11-11

Two legs ran with the same carrier, framing standard, writing rules and grounding block (the
canonical CoVe and citation block, the scope note and the independent-exploration block, verbatim
from the research prompt template). The objective and scope lines of each are reproduced here; the
founding-ask excerpts they carried are the owner's words recorded in the private record named in
[Founding Ask Coverage](#founding-ask-coverage).

```text
Leg 1 (tools). Objective: Find and verify the open-source tools, libraries and established
practice for generating, from ONE source, (a) a Python Click CLI's -h help output, (b) a canonical
CLI reference document in Markdown kept inside the repo, and (c) GitHub wiki pages, with an
automated check that fails when any of them drift from the source. Programmatic generation first;
say where only LLM-written prose is possible. Scope anchors (a floor, not a checklist):
mkdocs-click, sphinx-click, click-man, md-click, click-extra, typer utils docs, a small in-repo
generator; a data file both help and docs render from; cog or marker-region embedding;
snapshot/golden tests; --check generators; wiki publishing actions and their token needs; first-page
initialization; sidebars and footers; portability; feeding a later docs site. Verify the Click 8.5
truncation mechanism against the installed source.

Leg 2 (agentic). Objective: Find and verify what exists, as open-source tools, published research
and documented practice, for generating repository documentation with LLMs or agents, and how
teams combine it with programmatic generation so that derivable facts never come from an LLM and
the prose an LLM writes stays checked against the code. Scope anchors (a floor, not a checklist):
RepoAgent, DocAgent, readme-ai, deepwiki-open, OpenDeepWiki, Google Code Wiki, DeepWiki, Mintlify,
Swimm, Copilot or Claude Code as a doc agent in CI, drift detection, llms.txt, and published
accuracy evaluations; for each: output form, headless CI, provider lock-in, licence, maintenance,
measured accuracy; plus patterns that keep LLM prose honest.

Both: fetch at least one source per load-bearing claim in-session; never tag [VERIFIABLE] without a
fetched URL; apply the source-vetting rule (adoption and maintenance signals, or label exploratory);
append findings to an evidence file; end with gaps, blindspots and a ranked recommendation.
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

| Contributor | Family / model | Persona | Scope | Output |
|---|---|---|---|---|
| Research leg 1 | Claude Opus 5.5 | researcher, read-only | Click generators, drift checks, wiki publishing, docs sites | Evidence file, 68 cited sources |
| Research leg 2 | Claude Opus 5.5 | researcher, read-only | Agentic and LLM documentation generation | Evidence file, 51 cited sources |
| Synthesis | Claude Opus 5.5 | coordinator | Repository and Click measurements; this document | This document |

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- 2026-11-11: two research legs dispatched in parallel; the coordinator measured the Click
  mechanism, the command tree, the hand-written doc coverage and the wiki's state directly; this
  synthesis written from both evidence files and those measurements.

<!-- /doc:region name="run_history" -->
