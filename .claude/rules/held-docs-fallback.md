---
description: Touching anything under docs/held/ (a held item, a session's aggregate held review doc, a landing pad, or the directory's README) - read the canonical ai-harness docs/held/README.md in full first, hold from the kind's template with every referenced file and URL a Markdown link, compile, ship and land by the store's land mode, reap proved-stale pads and leaf worktrees, /sync-git after landing; when ai-harness is absent on this host, follow the repository's fallback README and surface the absence to the repo owner as a held `decision` in the `/format-decision` + `/explain` shape. Path-scoped.
paths:
  # Both the direct and the nested (`**/`) form are listed ON PURPOSE: whether Claude Code's
  # runtime matcher lets a middle `**` match ZERO segments is NOT verified, and a rule that
  # silently fails to fire enforces nothing. The redundancy costs nothing. This file is copied
  # byte for byte into project-template, so it carries no private tracking id.
  - "docs/held/**"
  - "**/docs/held/**"
---

# Held docs: read the canonical README first

You are touching the held store: everything a session shows the repo owner that he must review, answer or
act on. This rule has two copies with the same bytes. The one installed under `~/.claude/rules/`
by ai-harness is canonical; the one in a repository's `.claude/rules/` is copier-propagated from
project-template so that a host without ai-harness still loads it.

- Read `~/projects/ai-harness/docs/held/README.md` in full before touching anything under
  `docs/held/`. It is the contract: holding at emit time, the kinds and their TEMPLATE and STUB
  under `docs/prompts/held/`, the aggregate held review doc, answering, supersession, shipping and
  `/held-doc-review`. Read it there even when this repository carries its own copy, because the
  canonical copy may be newer.
- Canonical first: read the ai-harness version of the held README, of each kind's STUB.md and TEMPLATE.md and of every other held doc (~/projects/ai-harness/docs/held/ and ~/projects/ai-harness/docs/prompts/held/) before any repository-specific copy, and use a repository-specific version only for what it adds or changes.
- Links: write every file or doc a held body references as a Markdown link with display text, [📄 <repo>/<path>](<path relative to the item's directory>), and every URL (a Jira issue, a Confluence page, a pull request, anything else) as [<display text>](<URL>), never a bare path or URL; held_cli.py link prints the file form and held_cli.py add refuses a bare one (docs/held/README.md § Links).
- Before `add`, `scripts/held_links_check.py check <staging file>` runs the same scan.
- Every decision in a held doc has its own heading, linked from the doc's table of contents, and an approved one's heading ends ` ✅ Approved <date>`; `held_cli.py toc <file>` writes both before `add`, `answer` and `compile` add the marker, and `add` and `validate` refuse a body without them (docs/held/README.md § Decision headings and the approved marker).
- Every blocker needing owner input and every escalated decision reaches the repo owner only as a git-tracked held `decision` with `/format-decision` + a full `/explain`, shipped to main and listed in the session's compiled held doc before its absolute path is reported.
- Run `scripts/held_decision_check.py check <item>` and the strict format checker. Keep an AI-mooted
  question open beside its `AI-superseded:` review. Update linked Beads notes and CC titles when a ruling arrives.
- Hold with `held_cli.py add` from the kind's template in `config/held_kinds.toml`, then compile
  and ship, and merge or land the landing pad by its store's `land` mode. Reap a proved-stale
  landing pad or leaf worktree on sight with `/reap-worktree <path>`, and run `/sync-git` after
  landing.
- If `~/projects/ai-harness` is absent on this host, read this repository's own
  `docs/held/README.md` (the fallback copy) and follow it. Then surface the absence to the repo owner as a
  held `decision` in the `/format-decision` + `/explain` shape (the question: why ai-harness is not
  under the projects directory on this host, with the options and your recommendation), written
  by hand as a plain item file by that README's § File format, because there is no `held_cli.py`
  and no validator without ai-harness; when the fallback copy is missing too, the item says so.
  Run `scripts/format_decision_check.py --form chat` on it the first time ai-harness is present,
  and say all of this in the resume message's `STATUS`. Never treat the fallback copy as canonical.
