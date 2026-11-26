---
name: delete-comment
description: Run the comment-deletion pass on a named path, or the whole repository. Categorise every code comment as tautology, excuse, stale or decision record, delete it, file an issue per excuse, move decision records into the repository's decision-record directory, then lower comment-baseline.json.
---

# Delete comments

Repeat the comment-deletion pass on a path. The rule, the lexers and the decision-record
test are in `docs/procedures/comment-ratchet.md`; read it first.

The argument is a path (file or directory). With none, the pass covers the whole
repository.

## 1. List what is there

```bash
python3 scripts/comment_ratchet.py --list <path>
```

Each line is `path:line: text`. The last line, `total: N comments in F files; L
comment-only lines`, is the estimate before the pass: `L` lines go if every comment goes.
Keep it for the report.

If the repository has no baseline yet, fill it first, in its own commit:
`python3 scripts/comment_ratchet.py --init`.

## 2. Skip lines that come from the template

A file copier manages (it exists in the template) can hold two kinds of comment line:
lines from the template source, and lines this repository added. Only the second kind is
this pass's work. The template's lines are fixed in project-template, then arrive here
with a copier update.

To tell them apart, render the template at the commit this repository was last updated
to, and diff:

```bash
render=$(mktemp -d)
commit=$(sed -n 's/^_commit: //p' .copier-answers.yml)
src=$(sed -n 's/^_src_path: //p' .copier-answers.yml)
copier copy --trust --defaults --skip-tasks --vcs-ref "$commit" \
  --data-file .copier-answers.yml "$src" "$render/out"
diff "$render/out/<file>" "<file>"
```

`$src` may be replaced with a local clone of project-template (`~/projects/project-template`)
to avoid a network fetch; the `_commit` must exist in it.

Paths listed in `comment-skip.txt` are copied in by the fleet installer and are not
counted. Leave them alone: their comments are deleted at their source in ai-harness.
Paths listed in `comment-skip.local.txt` are files this repository does not author, such
as vendored bundles, and are not counted either. Leave them alone too.

A comment line that appears only on the repository side of the diff (`>`) is the
repository's. A comment line that also appears in the render is the template's: leave it,
and name it in the report as a template line. Remove the render directory with
`rm -r "$render"` when done. A repository without `.copier-answers.yml` owns every line.

## 3. Categorise every comment

Consecutive comment lines that make one point are one comment. Give each one kind:

| Kind | Test | Action |
|---|---|---|
| Decision record | States a rejected alternative, or a constraint the code alone does not show | Write it into a decision record, then delete the comment |
| Excuse | Explains away a hack: a swallowed error, a misnamed option, a known-wrong shortcut | Delete it, and file one issue for the hack |
| Stale | Names code, a tool or a behaviour that is no longer true | Delete it |
| Tautology | Everything else, section labels and summaries of the next line included | Delete it |

Apply the decision-record test strictly. A comment that labels a section, or explains what
a line does, is a tautology even when it reads as useful. Keep the categorised list
(`path:line`, kind, one-line reason) as the evidence for the pull request.

## 4. Act on each comment

- Decision records go in the repository's existing decision-record directory (such as
  `docs/decisions/`), else `docs/adr/NNNN-title.md`, numbered after the highest existing
  record. Records about template-owned files belong in project-template's
  `docs/adr/template/`. Group comments about one decision into one record. A record names
  the file it governs, the decision, the reason, and the rejected alternative where there
  is one.
- File each excuse as an issue in the repository's own Beads store, one issue per hack,
  naming `path:line` and what the hack does.
- Delete the comment without changing behaviour:
  - keep every tool directive (`noqa`, `shellcheck`, `eslint-disable`, `fmt: off` and the
    rest of the allowlist);
  - a Python block left with no statement needs `pass`;
  - a JSX comment goes with its braces: delete `{/* ... */}` whole;
  - in YAML, delete the whole comment line and keep the indentation of what is left;
  - a `.jinja` comment line may carry Jinja tags: keep any tag the render needs.
- Delete a blank line the deletion leaves doubled.

## 5. Lower the baseline and gate

```bash
python3 scripts/comment_ratchet.py --list <path>
python3 scripts/comment_ratchet.py --update
python3 scripts/comment_ratchet.py
```

Then run the repository's own gate (its linters and tests). `--update` refuses if any file
gained a comment; that means a deletion added one somewhere, so find it.

## 6. Report

Report, for the path:

- the `total:` line before and after, and the lines actually deleted
  (`git diff --stat`);
- the count per kind, and the decision records written;
- each excuse issue filed, with its `path:line`;
- template lines left for project-template;
- anything that could not be lexed.

The report and the decision records are read by people: give them the pre-ship pass in
`docs/procedures/ai-writing-anti-patterns.md` (in ai-harness) before committing, and have
a model that did not write them review them.
