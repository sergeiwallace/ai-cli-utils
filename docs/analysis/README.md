# Analysis Documents

Technical evaluations, tradeoff analyses, and architectural reasoning that surface from discussions and decision-making — preserved as reference for future revisiting.

## What belongs here

- **Tradeoff analyses** — "should we use X vs Y?" with pros/cons and reasoning (e.g., Python vs Rust, REST vs GraphQL, build vs buy)
- **Cross-cutting evaluations** — technical thinking that informs multiple design docs rather than living inside one specific sub-system design
- **Strategy analyses** — business/technical strategy reasoning (e.g., open-source vs proprietary, pricing model evaluation)
- **Retrospective analysis** — "we tried X, here's what we learned and why we switched to Y"

## What does NOT belong here

- **Sub-system designs** → `docs/designs/` (architecture blueprints for specific systems)
- **Implementation plans** → `docs/plans/` (task breakdowns, batching, execution guides)
- **External research** → `docs/research/` (web-sourced investigations with citations and prompt appendices)
- **How-to processes** → `docs/procedures/` (repeatable session config workflows)
- **Decisions within a design** → keep in the design doc's Decision Summary table + Decision Details section

## Guidelines

- **Not immutable.** Context changes. Revisit and update analyses when assumptions change. Add a dated "Revisited" section rather than rewriting — preserve the original reasoning.
- **Bar for inclusion:** "Would I want to reference this in 3 months?" If yes, write it up. If no, it's just a conversation.
- **Keep it lightweight.** 1-3 pages. These are reasoning snapshots, not comprehensive reports.
- **Frontmatter required.** Standard YAML frontmatter (`title`, `category: analysis`, `tags`, `status`, `source`).
