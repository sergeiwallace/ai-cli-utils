# Comment ratchet and the comment-deletion pass

Every repository rendered from project-template carries a lint rule that bans new code
comments, on a ratchet, and a `delete-comment` skill that removes the comments already
there. This page is the shape every repository follows. It is self-contained: a session
doing one repository's pass needs nothing else.

## The rule

`scripts/comment_ratchet.py` counts the comments in every git-tracked file it can lex and
compares each count with `comment-baseline.json` at the repository root.

- The baseline is a JSON object mapping a repo-relative path to its comment count:
  `{"scripts/build.py": 3}`. A file absent from it may have no comments. `{}` is the
  empty baseline.
- A file over its baseline fails: delete the new comment.
- A file under its baseline also fails until the baseline is lowered, so the count only
  goes down. `--update` lowers it and refuses to raise it.
- A file the lexer cannot read fails. An uncounted file is not a passing one. A file whose
  content is not the language its extension names (a regex list saved as `.toml`) is
  renamed, not excused.
- Files under `.beads/` are skipped, because bd writes them.
- Paths listed in `comment-skip.txt` or `comment-skip.local.txt` are skipped. See "Files
  no repository owns" below.
- Renaming a file that has baselined comments: move its entry in `comment-baseline.json`
  by hand in the same commit. `--update` cannot tell a rename from a new file, so it
  refuses, and the hand edit is visible in review.

It runs as the `comment-ratchet` hook in `.pre-commit-config.yaml` (pre-commit stage, the
same stage as ruff) and as `tests/test_comment_ratchet.py` in Python repositories.

Both files start with `# fmt: off`, like the other template-managed scripts, so a
consumer's formatter leaves them byte-identical. A consumer whose ruff selects complexity
rules (`C901`, `PLR0911`, `PLR0912`, `PLR0915`, `PLR2004`) or `T201` (the script reports
with `print`) lists the script under its own `per-file-ignores`, as it already does for
`scripts/session-broker.py`.

| Command | What it does |
|---|---|
| `python3 scripts/comment_ratchet.py` | Check every file. The test runs this. |
| `python3 scripts/comment_ratchet.py FILE ...` | Check only these files. The hook runs this with the staged files, so a commit costs only what it touches. |
| `python3 scripts/comment_ratchet.py --init` | Record every current count. Only works on an empty or missing baseline. |
| `python3 scripts/comment_ratchet.py --update` | Lower the baseline after deleting comments. Refuses if any file gained one. |
| `python3 scripts/comment_ratchet.py --list [PATH ...]` | Print each counted comment as `path:line: text`, then a `total:` line. |

## Files no repository owns

The fleet installer in ai-harness copies two hooks into every repository it manages:

- `.githooks/reference-transaction`, a copy of ai-harness's
  `.githooks/fleet/reference-transaction`;
- `.githooks/reference-transaction-fallback`, a byte copy of ai-harness's
  `.githooks/reference-transaction`.

The installer overwrites both on every run, so a deletion in the repository does not last.
When ai-harness deletes their comments at the source, a repository that counted them
would fail as "under baseline" with nobody there owning the fix.

So the ratchet skips the paths listed in two files at the repository root:

- `comment-skip.txt` is template-owned. Copier renders it and rewrites it on update.
- `comment-skip.local.txt` is repo-owned. The template ships no copy, so copier never
  renders or rewrites it. A missing file skips nothing extra.

Both files hold one repo-relative path per line, and a line ending in `/` skips
everything under it. A path in either file is skipped. A skipped path's entry in
`comment-baseline.json` is ignored, and the next `--update` drops it.

Copier renders `comment-skip.txt` from the hidden answer `owns_installer_hooks`, which is
false by default and then lists both hooks. Do not edit the file by hand, because copier
rewrites it on update: list a file the repository does not author, such as a vendored
"Do not edit" bundle, in `comment-skip.local.txt` instead. A new installer-managed path
is added in the template, so every repository gets it on its next update.

ai-harness holds the source of these hooks, so it must keep counting them. It answers
`owns_installer_hooks: true` once, in the commit of its copier update:

```bash
copier update --trust --data owns_installer_hooks=true
```

Copier records the answer in `.copier-answers.yml` and keeps it on every later update, so
its `comment-skip.txt` renders empty and its source hooks stay in its baseline.

## What counts as a comment

Languages are keyed by file extension. A trailing `.jinja` is stripped first, so
`ci.yml.jinja` is lexed as YAML. An extensionless file is classified by its shebang, and
`.envrc` is shell.

| Language | Extensions | How it is lexed | Directives that are not counted |
|---|---|---|---|
| Python | `.py`, `.pyi` | stdlib `tokenize`, so a `#` in a string is not a comment | `noqa`, `type:`, `pragma:`, `fmt: on/off/skip`, `pylint:`, `ruff:`, a line-1 shebang, a line-1 or line-2 encoding |
| Shell | `.sh`, `.bash`, `.zsh`, `.envrc` | quotes, `$(...)`, `${...}`, `$((...))`, backticks and heredoc bodies are not comments | `shellcheck`, a line-1 shebang |
| JS and TS | `.js`, `.mjs`, `.cjs`, `.ts`, `.mts`, `.cts`, `.jsx`, `.tsx` | strings, template literals with `${}`, regex literals and JSX text are not comments | `eslint-*`, `global`, `@ts-*`, `prettier-ignore`, `/// <reference>`, `istanbul`/`c8`/`v8 ignore`, `biome-ignore`, `@vitest-environment`, `@jest-environment`, `/*!`, `@license`, `@preserve`, a shebang |
| YAML | `.yml`, `.yaml` | quoted scalars and block scalars (`run: \|`) are not comments | `yaml-language-server:`, `yamllint disable/enable`, `pragma:` |
| TOML | `.toml` | every string form, multi-line ones included | `#:schema` |
| gitignore | `.gitignore` | a line starting with `#` | none |

A Python docstring is not a comment token, and a JSDoc `/** ... */` block is lexed but not
counted. Both are left for a later pass that decides what to do with API documentation.

A repository with its own guard markers (a comment a hook greps for, such as
`# focus-ok:`) lists one marker per line in `comment-directives.txt` at its root. A comment
starting with a listed marker is not counted.

Not covered yet: Markdown and HTML comments, INI-style files (`.editorconfig`,
`pytest.ini`, `.env` files), JSON (which has no comments), CSS, Vue, Svelte and Astro, Go,
Rust, and Jinja's own `{# ... #}` comments, which never reach a rendered repository.

## Categorising each comment

Each comment is one of four kinds, and each kind has one action.

| Kind | Test | Action |
|---|---|---|
| Tautology | Anything that is not one of the other three, including section labels and summaries of the next line | Delete |
| Stale | Names code, a tool or a behaviour that is no longer true | Delete |
| Excuse | Explains away a hack: a swallowed error, a misnamed option, a known-wrong shortcut | Delete, and file one issue per excuse in the repository's own Beads store, naming file:line and the hack |
| Decision record | States a rejected alternative, or a constraint that the code alone does not show | Move into a decision record, then delete |

A comment that only labels a section, or says what the next line does, is a tautology even
when it reads as helpful. Several consecutive comment lines that make one point are one
comment for this purpose.

## Where things go

- Decision records go in the repository's existing decision-record directory (for
  example `docs/decisions/`). With none, use `docs/adr/NNNN-title.md`. Records for
  template-owned files are already in `docs/adr/template/`, delivered by copier.
- The skill goes where the repository's skills are installed from. With no such place,
  use `.claude/skills/delete-comment/`, which is where copier puts it.

## The order for an existing repository

1. Run the copier update first (`ai copier-update`, or `copier update --trust`). This
   brings the ratchet, the hook, the skill, `comment-skip.txt` and the cleaned
   template-owned files. A repository whose baseline already lists the installer hooks
   drops those entries with `--update`.
2. Fill the baseline with the comments the repository still owns, in the same commit:

   ```bash
   python3 scripts/comment_ratchet.py --init
   ```

3. Run the pass with the `delete-comment` skill on the repository's own code, then lower
   the baseline:

   ```bash
   python3 scripts/comment_ratchet.py --update
   ```

The template pass already removed the comments that come from the template, so a
repository pass covers only comment lines the repository owns. That includes lines a
repository added to a template-owned file such as `.gitignore` or
`.pre-commit-config.yaml`. The skill tells the two apart by diffing against a fresh render
of the template at the commit in `.copier-answers.yml`.

## Estimating lines shed

`python3 scripts/comment_ratchet.py --list` ends with
`total: N comments in F files; L comment-only lines`. `L` is the number of lines deleting
every counted comment would remove: a comment on its own line removes its lines, and a
comment after code on the same line removes none. Report `L` before the pass, the lines
actually deleted, and the `total:` line after it.

## Decisions

Each decision lists what was chosen, why, and the rejected alternative.

1. One script with a small lexer per language, keyed by extension, rather than one script
   per language. A Python-only repository uses the same file unchanged, and a JS
   repository gets a working JS path without any Python lexer involved.
2. A decision record must state a rejected alternative or a constraint the code does not
   show. Rejected: counting section-style comments as decision records, which turned 48
   comments into 21 records in an earlier pilot.
3. Decision records go in the repository's existing directory, else `docs/adr/`.
   Template-owned records sit in `docs/adr/template/` so their numbers never collide with
   a repository's own `docs/adr/NNNN` records.
4. The skill goes where the repository's skills are installed from, else
   `.claude/skills/delete-comment/`.
5. The ratchet is wired into the existing pre-commit config and the existing pytest gate.
   No new hook framework.
6. Docstrings and JSDoc blocks are out of scope for this pass. Most JSDoc comments are API
   descriptions that editors and doc generators read, so deleting them is its own
   decision. Rejected: counting JSDoc as ordinary comments.
7. Lines a repository added to a template-owned file belong to that repository's pass.
   Rejected: excluding template-owned paths wholesale, which leaves those lines covered by
   neither pass. This is why `.gitignore`, YAML and TOML are lexed.
8. `--init` exists beside `--update`. Adopting the rule in an existing repository needs
   one command that records what is there, and `--update` must never raise a count. Rejected:
   letting `--update` raise counts when the baseline is empty, which hides the one
   operation that grows the baseline inside the one that shrinks it.
9. The template ships no `comment-baseline.json`; a missing baseline is the empty one,
   and `--init` creates it. So copier never manages a repository's baseline. Rejected:
   shipping `{}` under `_skip_if_exists`, which copier re-creates after a deliberate
   deletion and which the template's resurrection gate refuses while no consumer has the
   file; and shipping `{}` as a managed file, which conflicts on update in any repository
   that already has a baseline.
10. A file the lexer cannot read fails the check. Rejected: skipping it with a warning,
    which lets a whole file of comments through unseen.
11. Repository guard markers live in `comment-directives.txt`, not in the script. The
    script is template-owned, so a marker added to it by one repository would be lost on
    the next copier update.
12. `.jsx` and `.tsx` are covered with a JSX-aware lexer. A `.tsx` generic arrow written
    `<T,>` or `<T extends U>` is read as a type parameter, not as JSX.
13. The skill is not locked with `disable-model-invocation`, because per-repository
    delegation briefs invoke it. Its edits are local and reviewed in the pull request.
14. The hook checks only the staged files; the test and a bare run check every file. A
    full scan of a 3,800-file repository took about 17 CPU seconds on a loaded host,
    mostly in Python's `tokenize`, which is too slow for every commit. A file outside the
    commit cannot change its count, so nothing is missed; a baseline entry left stale by
    a deleted file is caught by the test and by `--update`. Rejected: a full scan on every
    commit.
15. Files the fleet installer manages are skipped through a copier-rendered
    `comment-skip.txt`, whose contents follow the hidden answer `owns_installer_hooks`.
    Anyone reading the repository can open the list. A change to it reaches every
    repository on its next copier update. ai-harness, which owns the hook source, sets
    one recorded answer and never edits a managed file.
    - Rejected: a skip file each repository edits. ai-harness's edit would conflict on
      every copier update that changes the list.
    - Rejected: the same file under `_skip_if_exists`. A path added later would never
      reach a repository that already has the file.
    - Rejected: a path list inside the script with an opt-out. The rule would sit in code
      that no repository reads, and the opt-out would still need a config file.
    - Rejected: each repository lowering its baseline after every installer refresh. That
      turns every repository red on each hook change.
16. Files a repository does not author but copier does not manage either, such as a
    vendored "Do not edit" bundle, are skipped through `comment-skip.local.txt`. The
    repository owns that file. The template ships no copy and does not list it under
    `_skip_if_exists`, so copier never renders, rewrites or merges it.
    - Rejected: a marked repository section inside the rendered `comment-skip.txt`.
      Copier's 3-way merge would have to keep the hand-written lines, and any template
      change to the file would then conflict in every repository that has a section.
    - Rejected: keeping vendored bundles in the baseline. Each bundle refresh moves the
      count and turns the ratchet red until someone runs `--update`, and a "Do not edit"
      bundle is not the repository's code to clean.
