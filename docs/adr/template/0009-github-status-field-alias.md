# 0009. The Project's Status field keeps the `Todo` alias

Governs: `.github/project.yml`.

## Decision

The built-in Status option `To Do` lists `aliases: [Todo]`.

## Why

`Todo` is GitHub's default option. The alias makes the setup rename that option rather
than create a new one, so it keeps its id and no item loses its Status. Removing the alias
would orphan every item still on `Todo`.

The rest of the manifest's behaviour (what the setup creates, updates and never deletes)
is documented in ai-harness's `docs/procedures/github-project-setup.md`.
