# 0005. Markdownlint rules relaxed for the fleet's documents

Governs: `.markdownlint.yaml`, `docs/research/.markdownlint.yaml`.

## Decision and reasons

Each relaxed rule, with the reason it is off:

| Rule | Setting | Reason |
|---|---|---|
| MD013 line length | off | Editors wrap long lines for display. |
| MD041 first line H1 | off | Agent files, signal files and templates need no heading. |
| MD025 single H1 | `front_matter_title: ""` | Research and design docs carry both a frontmatter title and a body H1. |
| MD051 link fragments | off | Anchors with special characters and generated READMEs break the checker. |
| MD047 final newline | off | Generated files often end without one. |
| MD007 list indentation | off | Generated docs indent lists in varied ways. |
| MD029 ordered list prefix | off | Some lists deliberately start above 1, for example CLAUDE.md startup steps that continue the global config's numbering. |
| MD040 fenced code language | off | Design and plan docs use plain blocks for shell sessions, config snippets and ASCII diagrams. |
| MD028 blank line in blockquote | off | Feedback sections in design and plan docs use blank lines inside quotes for readability. |
| MD003 heading style | off | Feedback sections use `> ---` as a divider, which markdownlint misreads as a setext heading underline. |
| MD030 list marker spacing | off | Docs use two-space indented lists. |
| MD026 heading punctuation | off | Headings such as "Key:", "Format:" and "Note:" are common. |
| MD056 table column count | off | Docs use multi-line and empty cells. |
| MD034 bare URLs | off | Reference docs use bare URLs for readability. |
| MD004 bullet style | off | Docs mix `*` and `-` bullets. |
| MD001 heading increments | off | Design doc appendices skip from h2 to h4 inside decision sections. |
| MD049, MD050 emphasis style | asterisk | markdownlint 0.23 flags mixed `_` and `*` emphasis, so one style is fixed. |
| MD038 spaces in code spans | off | Audit and plan docs quote literal patterns with trailing spaces, such as the conflict marker `^<<<<<<< `. |

`docs/research/` extends the root file through an explicit path and relaxes a further set
of rules, because generated research output (Gemini, deep research) does not conform to
the defaults.
